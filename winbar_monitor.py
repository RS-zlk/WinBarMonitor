#!/usr/bin/env python3
"""Remote Windows metrics collector and SwiftBar rendering helpers.

The collector only invokes read-only PowerShell/CIM queries and nvidia-smi over
SSH.  It is intentionally dependency-free so it can run with macOS' Python 3.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_REFRESH_SECONDS = 30
DEFAULT_SSH_TIMEOUT = 15
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "winbar-monitor" / "cache.json"


POWERSHELL_SCRIPT = r'''
$os = Get-CimInstance -ClassName Win32_OperatingSystem
$cpuRow = Get-CimInstance -ClassName Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
$disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='C:'"
$netRows = @(Get-CimInstance -ClassName Win32_PerfFormattedData_Tcpip_NetworkInterface)
$procRows = @(Get-CimInstance -ClassName Win32_PerfFormattedData_PerfProc_Process |
  Where-Object { $_.Name -notmatch '^(Idle|_Total)$' } |
  Sort-Object -Property PercentProcessorTime -Descending |
  Select-Object -First 5)
$gpuRows = @()
try {
  $gpuLines = @(nvidia-smi --query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw --format=csv,noheader,nounits 2>$null)
  foreach ($line in $gpuLines) {
    $parts = $line -split ',' | ForEach-Object { $_.Trim() }
    if ($parts.Count -ge 7) {
      $gpuRows += [pscustomobject]@{
        name=$parts[0]; utilization_gpu=$parts[1]; utilization_memory=$parts[2];
        memory_used=$parts[3]; memory_total=$parts[4]; temperature=$parts[5]; power_draw=$parts[6]
      }
    }
  }
} catch {}
$netRx = ($netRows | Measure-Object -Property BytesReceivedPersec -Sum).Sum
$netTx = ($netRows | Measure-Object -Property BytesSentPersec -Sum).Sum
[pscustomobject]@{
  cpu_percent=$cpuRow.PercentProcessorTime
  memory_total_bytes=([double]$os.TotalVisibleMemorySize * 1024)
  memory_free_bytes=([double]$os.FreePhysicalMemory * 1024)
  disk_total_bytes=$disk.Size
  disk_free_bytes=$disk.FreeSpace
  network_rx_bps=$netRx
  network_tx_bps=$netTx
  last_boot=$os.LastBootUpTime.ToString('o')
  processes=@($procRows | ForEach-Object { [pscustomobject]@{name=$_.Name; cpu_percent=$_.PercentProcessorTime; pid=$_.IDProcess; memory_bytes=$_.WorkingSetPrivate} })
  gpus=$gpuRows
} | ConvertTo-Json -Compress -Depth 6
'''


@dataclass(frozen=True)
class Config:
    ssh_alias: str = "y9000p-remote"
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    ssh_timeout: int = DEFAULT_SSH_TIMEOUT
    cache_path: Path = DEFAULT_CACHE_PATH

    @classmethod
    def from_env(cls) -> "Config":
        def positive_int(name: str, default: int) -> int:
            try:
                value = int(os.environ.get(name, default))
                return max(1, value)
            except (TypeError, ValueError):
                return default

        path = os.environ.get("WINBAR_CACHE_PATH")
        return cls(
            ssh_alias=os.environ.get("WINBAR_SSH_ALIAS", cls.ssh_alias),
            refresh_seconds=positive_int("WINBAR_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS),
            ssh_timeout=positive_int("WINBAR_SSH_TIMEOUT", DEFAULT_SSH_TIMEOUT),
            cache_path=Path(path).expanduser() if path else DEFAULT_CACHE_PATH,
        )


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def _first_json_object(text: str) -> dict[str, Any]:
    """Extract JSON even if OpenSSH/PowerShell prepends a warning line."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise ValueError("远端没有返回有效的 JSON 指标")


def parse_gpu_csv(text: str) -> list[dict[str, Any]]:
    """Parse nvidia-smi CSV output; N/A values become None."""
    result: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            continue
        result.append({
            "name": parts[0],
            "utilization_gpu": _number(parts[1]),
            "utilization_memory": _number(parts[2]),
            "memory_used": _number(parts[3]),
            "memory_total": _number(parts[4]),
            "temperature": _number(parts[5]),
            "power_draw": _number(parts[6]),
        })
    return result


def normalize_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize PowerShell JSON and tolerate missing/N/A GPU fields."""
    metrics = dict(raw)
    for key in ("cpu_percent", "memory_total_bytes", "memory_free_bytes", "disk_total_bytes",
                "disk_free_bytes", "network_rx_bps", "network_tx_bps"):
        metrics[key] = _number(metrics.get(key))
    gpus = metrics.get("gpus") or []
    if isinstance(gpus, dict):
        gpus = [gpus]
    normalized_gpus = []
    for gpu in gpus:
        gpu = gpu if isinstance(gpu, dict) else {}
        normalized_gpus.append({
            "name": str(gpu.get("name") or "NVIDIA GPU"),
            "utilization_gpu": _number(gpu.get("utilization_gpu")),
            "utilization_memory": _number(gpu.get("utilization_memory")),
            "memory_used": _number(gpu.get("memory_used")),
            "memory_total": _number(gpu.get("memory_total")),
            "temperature": _number(gpu.get("temperature")),
            "power_draw": _number(gpu.get("power_draw")),
        })
    metrics["gpus"] = normalized_gpus
    metrics["processes"] = metrics.get("processes") if isinstance(metrics.get("processes"), list) else []
    return metrics


def collect_remote(config: Config) -> dict[str, Any]:
    encoded = base64.b64encode(POWERSHELL_SCRIPT.encode("utf-16le")).decode("ascii")
    command = [
        "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={min(config.ssh_timeout, 5)}",
        "-o", "ControlMaster=auto", "-o", "ControlPersist=60",
        "-o", "ControlPath=~/.ssh/winbar-%C",
        config.ssh_alias, "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   errors="replace", timeout=config.ssh_timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"SSH/PowerShell 采集超时（{config.ssh_timeout} 秒）") from None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"ssh 返回码 {completed.returncode}")
    return normalize_metrics(_first_json_object(completed.stdout))


def read_cache(path: Path) -> tuple[dict[str, Any] | None, float | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("metrics"), float(payload.get("collected_at"))
    except (OSError, ValueError, TypeError, KeyError):
        return None, None


def write_cache(path: Path, metrics: dict[str, Any], collected_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({"metrics": metrics, "collected_at": collected_at}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _fmt_rate(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    units = ("B/s", "KB/s", "MB/s", "GB/s")
    index = 0
    while abs(number) >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.1f} {units[index]}"


def _fmt_bytes(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    units = ("B", "GB", "TB")
    number /= 1024 ** 3
    index = 1
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.1f} {units[index]}"


def _percent(value: Any) -> str:
    number = _number(value)
    return "N/A" if number is None else f"{number:.0f}%"


def _uptime(last_boot: Any, now: float | None = None) -> str:
    if not last_boot:
        return "N/A"
    try:
        boot_text = str(last_boot).replace("Z", "+00:00")
        # Windows CIM commonly emits seven fractional-second digits, while the
        # system Python bundled with older macOS releases accepts at most six.
        boot_text = re.sub(r"(\.\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r"\1", boot_text)
        boot = datetime.fromisoformat(boot_text)
        if boot.tzinfo is None:
            boot = boot.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - boot).total_seconds()))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes = seconds // 60
        return f"{days}天 {hours}时 {minutes}分" if days else f"{hours}时 {minutes}分"
    except (TypeError, ValueError, OverflowError):
        return "N/A"


def render(metrics: dict[str, Any], *, stale: bool = False, error: str | None = None,
           collected_at: float | None = None, plugin_name: str = "winbar.1m.py") -> str:
    gpu = (metrics.get("gpus") or [{}])[0]
    cpu = _percent(metrics.get("cpu_percent"))
    gpu_util = _percent(gpu.get("utilization_gpu"))
    temperature = gpu.get("temperature")
    power_draw = gpu.get("power_draw")
    temperature_text = f"{temperature:.0f}°C" if temperature is not None else "N/A"
    power_text = f"{power_draw:.0f} W" if power_draw is not None else "N/A"
    state = "🟡 缓存" if stale else "🟢 在线"
    header = f"{state} · GPU {gpu_util} · CPU {cpu}"
    lines = [header, "---", f"🖥️ Windows · {metrics.get('hostname', 'y9000p')}",
             f"CPU 占用 · {cpu}", f"内存 · {_fmt_bytes(metrics.get('memory_total_bytes') - metrics.get('memory_free_bytes')) if metrics.get('memory_total_bytes') is not None and metrics.get('memory_free_bytes') is not None else 'N/A'} / {_fmt_bytes(metrics.get('memory_total_bytes'))}",
             f"C盘 · {_fmt_bytes(metrics.get('disk_total_bytes') - metrics.get('disk_free_bytes')) if metrics.get('disk_total_bytes') is not None and metrics.get('disk_free_bytes') is not None else 'N/A'} / {_fmt_bytes(metrics.get('disk_total_bytes'))}",
             f"网络 · ↓ {_fmt_rate(metrics.get('network_rx_bps'))} · ↑ {_fmt_rate(metrics.get('network_tx_bps'))}",
             f"运行时间 · {_uptime(metrics.get('last_boot'))}", "---",
             f"🎮 {gpu.get('name', 'NVIDIA GPU')}",
             f"GPU 利用率 · {gpu_util}",
             f"显存 · {_fmt_bytes(gpu.get('memory_used') * 1024 ** 2) if gpu.get('memory_used') is not None else 'N/A'} / {_fmt_bytes(gpu.get('memory_total') * 1024 ** 2) if gpu.get('memory_total') is not None else 'N/A'}",
             f"温度 · {temperature_text}",
             f"功耗 · {power_text}", "---", "🔥 Top 进程"]
    processes = metrics.get("processes") or []
    for proc in processes[:5]:
        name = str(proc.get("name") or "?")[:24]
        lines.append(f"{name} · CPU {_percent(proc.get('cpu_percent'))} · PID {proc.get('pid', 'N/A')}")
    if error:
        lines.extend(["---", f"⚠️ {error}"])
    if collected_at:
        lines.append(f"---\n🕒 {datetime.fromtimestamp(collected_at).astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.extend(["---", f"🔄 手动刷新 | refresh=true", f"🔐 打开 SSH | bash=/usr/bin/ssh param1={os.environ.get('WINBAR_SSH_ALIAS', 'y9000p-remote')} terminal=true"])
    return "\n".join(lines)


def main() -> int:
    config = Config.from_env()
    now = time.time()
    cached, cached_at = read_cache(config.cache_path)
    try:
        metrics = collect_remote(config)
        collected_at = now
        write_cache(config.cache_path, metrics, collected_at)
        print(render(metrics, collected_at=collected_at))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        if cached:
            print(render(normalize_metrics(cached), stale=True, error=f"连接失败：{exc}", collected_at=cached_at))
        else:
            print(f"🔴 离线 · Windows\n---\n⚠️ {exc}\n---\n🔄 手动刷新 | refresh=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
