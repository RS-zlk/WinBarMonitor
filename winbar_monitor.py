#!/usr/bin/env python3
"""Remote Windows metrics collector and SwiftBar rendering helpers.

The collector only invokes read-only PowerShell/CIM queries and nvidia-smi over
SSH.  It is intentionally dependency-free so it can run with macOS' Python 3.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shlex
import sqlite3
import struct
import subprocess
import sys
import time
import zlib
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_REFRESH_SECONDS = 30
DEFAULT_SSH_TIMEOUT = 15
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "winbar-monitor" / "cache.json"
DEFAULT_HISTORY_PATH = Path.home() / ".local" / "share" / "winbar-monitor" / "history.sqlite3"
DEFAULT_REPORT_PATH = Path.home() / ".local" / "share" / "winbar-monitor" / "history.html"
DEFAULT_SETTINGS_PATH = Path.home() / ".local" / "share" / "winbar-monitor" / "settings.json"
DEFAULT_ALERT_STATE_PATH = Path.home() / ".local" / "share" / "winbar-monitor" / "low-usage-alert-state.json"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name(".winbar.env")
MENU_BAR_MODES = ("auto", "gpu", "cpu", "vram", "temperature", "power", "network")
MENU_BAR_MODE_LABELS = {
    "auto": "智能概览（优先 GPU）", "gpu": "GPU 利用率", "cpu": "CPU 利用率",
    "vram": "显存占用", "temperature": "GPU 温度", "power": "GPU 功耗",
    "network": "网络下载速率",
}
DEFAULT_LOW_USAGE_GPU_THRESHOLD = 5.0
DEFAULT_LOW_USAGE_VRAM_THRESHOLD = 10.0
DEFAULT_LOW_USAGE_DURATION_SECONDS = 5 * 60


POWERSHELL_SCRIPT = r'''
$os = Get-CimInstance -ClassName Win32_OperatingSystem
$computer = $env:COMPUTERNAME
if (-not $computer) {
  try { $computer = (Get-CimInstance -ClassName Win32_ComputerSystem).Name } catch { $computer = $null }
}
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
  hostname=$computer
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
class LowUsageAlertConfig:
    """User-configurable definition of a likely-finished GPU workload."""

    enabled: bool = False
    gpu_utilization_threshold: float = DEFAULT_LOW_USAGE_GPU_THRESHOLD
    vram_utilization_threshold: float = DEFAULT_LOW_USAGE_VRAM_THRESHOLD
    duration_seconds: int = DEFAULT_LOW_USAGE_DURATION_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "gpu_utilization_threshold": self.gpu_utilization_threshold,
            "vram_utilization_threshold": self.vram_utilization_threshold,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class LowUsageAlertStatus:
    """The current detector state used for menu feedback and notifications."""

    monitoring: bool = False
    low_since: float | None = None
    notified: bool = False
    max_gpu_utilization: float | None = None
    vram_utilization: float | None = None


@dataclass(frozen=True)
class Config:
    ssh_alias: str = "windows-monitor"
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    ssh_timeout: int = DEFAULT_SSH_TIMEOUT
    cache_path: Path = DEFAULT_CACHE_PATH
    history_path: Path = DEFAULT_HISTORY_PATH
    report_path: Path = DEFAULT_REPORT_PATH
    retention_days: int = DEFAULT_RETENTION_DAYS
    settings_path: Path = DEFAULT_SETTINGS_PATH
    alert_state_path: Path = DEFAULT_ALERT_STATE_PATH
    menu_bar_mode: str = "auto"
    low_usage_alert: LowUsageAlertConfig = LowUsageAlertConfig()

    @classmethod
    def from_env(cls) -> "Config":
        config_path = Path(os.environ.get("WINBAR_CONFIG_PATH", DEFAULT_CONFIG_PATH)).expanduser()
        local = _read_env_file(config_path)

        def value(name: str, default: Any) -> Any:
            return os.environ.get(name, local.get(name, default))

        def positive_int(name: str, default: int) -> int:
            try:
                parsed = int(value(name, default))
                return max(1, parsed)
            except (TypeError, ValueError):
                return default

        cache_path = value("WINBAR_CACHE_PATH", None)
        history_path = value("WINBAR_HISTORY_PATH", None)
        report_path = value("WINBAR_REPORT_PATH", None)
        settings_path = value("WINBAR_SETTINGS_PATH", None)
        alert_state_path = value("WINBAR_ALERT_STATE_PATH", None)
        resolved_settings_path = Path(settings_path).expanduser() if settings_path else DEFAULT_SETTINGS_PATH
        saved_settings = _read_settings(resolved_settings_path)
        menu_bar_mode = str(os.environ.get("WINBAR_MENU_BAR_MODE", saved_settings.get(
            "menu_bar_mode", local.get("WINBAR_MENU_BAR_MODE", cls.menu_bar_mode))))
        saved_alert = saved_settings.get("low_usage_alert")
        saved_alert = saved_alert if isinstance(saved_alert, dict) else {}

        def alert_value(environment_name: str, setting_name: str) -> Any:
            """Runtime variables win; menu settings win over static local config."""
            return os.environ.get(environment_name, saved_alert.get(setting_name, local.get(environment_name)))

        low_usage_alert = _low_usage_alert_config(
            None,
            enabled=alert_value("WINBAR_LOW_USAGE_ALERT_ENABLED", "enabled"),
            gpu_threshold=alert_value("WINBAR_LOW_USAGE_GPU_THRESHOLD", "gpu_utilization_threshold"),
            vram_threshold=alert_value("WINBAR_LOW_USAGE_VRAM_THRESHOLD", "vram_utilization_threshold"),
            duration_seconds=alert_value("WINBAR_LOW_USAGE_DURATION_SECONDS", "duration_seconds"),
        )
        return cls(
            ssh_alias=str(value("WINBAR_SSH_ALIAS", cls.ssh_alias)),
            refresh_seconds=positive_int("WINBAR_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS),
            ssh_timeout=positive_int("WINBAR_SSH_TIMEOUT", DEFAULT_SSH_TIMEOUT),
            cache_path=Path(cache_path).expanduser() if cache_path else DEFAULT_CACHE_PATH,
            history_path=Path(history_path).expanduser() if history_path else DEFAULT_HISTORY_PATH,
            report_path=Path(report_path).expanduser() if report_path else DEFAULT_REPORT_PATH,
            retention_days=positive_int("WINBAR_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
            settings_path=resolved_settings_path,
            alert_state_path=Path(alert_state_path).expanduser() if alert_state_path else DEFAULT_ALERT_STATE_PATH,
            menu_bar_mode=menu_bar_mode if menu_bar_mode in MENU_BAR_MODES else cls.menu_bar_mode,
            low_usage_alert=low_usage_alert,
        )


def _read_env_file(path: Path) -> dict[str, str]:
    """Read simple KEY=VALUE settings without executing the local file."""
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"WINBAR_[A-Z0-9_]+|SWIFTBAR_PLUGIN_DIR", name):
            continue
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            continue
        result[name] = parts[0] if parts else ""
    return result


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    return default


def _bounded_percent(value: Any, default: float) -> float:
    number = _number(value)
    return number if number is not None and 0 <= number <= 100 else default


def _positive_seconds(value: Any, default: int) -> int:
    number = _number(value)
    return int(number) if number is not None and number >= 1 else default


def _low_usage_alert_config(saved: Any = None, *, enabled: Any = None,
                            gpu_threshold: Any = None, vram_threshold: Any = None,
                            duration_seconds: Any = None) -> LowUsageAlertConfig:
    saved = saved if isinstance(saved, dict) else {}
    return LowUsageAlertConfig(
        enabled=_as_bool(enabled, _as_bool(saved.get("enabled"), False)) if enabled is not None
        else _as_bool(saved.get("enabled"), False),
        gpu_utilization_threshold=_bounded_percent(
            gpu_threshold if gpu_threshold is not None else saved.get("gpu_utilization_threshold"),
            DEFAULT_LOW_USAGE_GPU_THRESHOLD,
        ),
        vram_utilization_threshold=_bounded_percent(
            vram_threshold if vram_threshold is not None else saved.get("vram_utilization_threshold"),
            DEFAULT_LOW_USAGE_VRAM_THRESHOLD,
        ),
        duration_seconds=_positive_seconds(
            duration_seconds if duration_seconds is not None else saved.get("duration_seconds"),
            DEFAULT_LOW_USAGE_DURATION_SECONDS,
        ),
    )


def _write_settings(path: Path, updates: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = _read_settings(path)
    settings.update(updates)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    path.chmod(0o600)


def save_menu_bar_mode(path: Path, mode: str) -> None:
    """Save a user-selected display mode without modifying source configuration."""
    if mode not in MENU_BAR_MODES:
        raise ValueError(f"未知的菜单栏显示模式：{mode}")
    _write_settings(path, {"menu_bar_mode": mode})


def save_low_usage_alert_config(path: Path, alert: LowUsageAlertConfig) -> None:
    """Persist alert preferences while preserving unrelated local settings."""
    _write_settings(path, {"low_usage_alert": alert.as_dict()})


def _refresh_installed_plugin() -> None:
    """Notify SwiftBar immediately after a display-setting change."""
    library_dir = Path(__file__).resolve().parent
    if library_dir.name != ".winbar_lib":
        return
    for plugin_path in library_dir.parent.glob("winbar.*s.py"):
        try:
            plugin_path.touch()
        except OSError:
            pass


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
    hostname = metrics.get("hostname")
    metrics["hostname"] = str(hostname).strip() if hostname else None
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


def _read_alert_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_alert_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    path.chmod(0o600)


def _low_usage_measurement(metrics: dict[str, Any]) -> tuple[float, float] | None:
    """Return the maximum per-GPU work and VRAM use, or None when unknown.

    A workload may run on any available GPU, so every GPU must be below both
    thresholds.  Using the highest per-GPU VRAM percentage avoids a busy GPU
    being masked by another GPU with little allocated memory.
    """
    gpus = metrics.get("gpus") or []
    if not gpus:
        return None
    gpu_utilizations = [_number(gpu.get("utilization_gpu")) for gpu in gpus]
    memory_used = [_number(gpu.get("memory_used")) for gpu in gpus]
    memory_total = [_number(gpu.get("memory_total")) for gpu in gpus]
    if (any(value is None for value in gpu_utilizations) or any(value is None for value in memory_used)
            or any(value is None or value <= 0 for value in memory_total)):
        return None
    vram_utilizations = [used / total * 100 for used, total in zip(memory_used, memory_total)]
    return max(gpu_utilizations), max(vram_utilizations)


def _alert_config_signature(alert: LowUsageAlertConfig) -> dict[str, Any]:
    """Changing a rule starts a fresh observation period to avoid false alerts."""
    return alert.as_dict()


def update_low_usage_alert(path: Path, alert: LowUsageAlertConfig,
                           metrics: dict[str, Any], collected_at: float) -> tuple[LowUsageAlertStatus, bool]:
    """Advance the persisted detector and return whether a notification is due.

    Only successful, complete GPU samples participate.  Cached or missing GPU
    readings therefore cannot start or extend the low-usage timer.
    """
    measurement = _low_usage_measurement(metrics)
    if not alert.enabled:
        # Clear a prior run once so toggling the feature off and back on starts
        # a fresh observation window, while avoiding writes for a new default
        # installation where alerts have never been enabled.
        if _read_alert_state(path):
            _write_alert_state(path, {})
        return LowUsageAlertStatus(), False

    state = _read_alert_state(path)
    signature = _alert_config_signature(alert)
    if state.get("config") != signature:
        state = {"config": signature, "low_since": None, "notified": False}

    if measurement is None:
        state.update({"low_since": None, "notified": False})
        _write_alert_state(path, state)
        return LowUsageAlertStatus(monitoring=True), False

    max_gpu_utilization, vram_utilization = measurement
    is_low = (max_gpu_utilization <= alert.gpu_utilization_threshold
              and vram_utilization <= alert.vram_utilization_threshold)
    if not is_low:
        state.update({"low_since": None, "notified": False})
        _write_alert_state(path, state)
        return LowUsageAlertStatus(
            monitoring=True,
            max_gpu_utilization=max_gpu_utilization,
            vram_utilization=vram_utilization,
        ), False

    low_since = _number(state.get("low_since"))
    if low_since is None or low_since > collected_at:
        low_since = collected_at
        state.update({"low_since": low_since, "notified": False})
    notified = _as_bool(state.get("notified"), False)
    should_notify = not notified and collected_at - low_since >= alert.duration_seconds
    _write_alert_state(path, state)
    return LowUsageAlertStatus(
        monitoring=True,
        low_since=low_since,
        notified=notified,
        max_gpu_utilization=max_gpu_utilization,
        vram_utilization=vram_utilization,
    ), should_notify


def mark_low_usage_alert_notified(path: Path, alert: LowUsageAlertConfig) -> None:
    """Record delivery only after macOS has accepted the notification."""
    state = _read_alert_state(path)
    if state.get("config") == _alert_config_signature(alert):
        state["notified"] = True
        _write_alert_state(path, state)


def send_low_usage_notification(hostname: str, alert: LowUsageAlertConfig,
                                status: LowUsageAlertStatus) -> None:
    """Display one native macOS notification without introducing dependencies."""
    host = hostname or "Windows PC"
    gpu = status.max_gpu_utilization
    vram = status.vram_utilization
    message = (
        f"{host} 的 GPU 利用率为 {gpu:.0f}%（阈值 {alert.gpu_utilization_threshold:g}%），"
        f"最高显存占用为 {vram:.0f}%（阈值 {alert.vram_utilization_threshold:g}%），"
        f"已持续 {_fmt_duration(alert.duration_seconds)}。"
    )
    script = (
        f"display notification {json.dumps(message, ensure_ascii=False)} "
        f"with title {json.dumps('WinBarMonitor：任务可能已完成', ensure_ascii=False)}"
    )
    try:
        completed = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True,
                                   text=True, errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"无法发送 macOS 通知：{exc}") from None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "无法发送 macOS 通知")


HISTORY_COLUMNS = (
    "cpu_percent", "memory_used_bytes", "memory_total_bytes", "disk_used_bytes",
    "disk_total_bytes", "network_rx_bps", "network_tx_bps", "gpu_percent",
    "gpu_memory_used_bytes", "gpu_memory_total_bytes", "gpu_temperature", "gpu_power_watts",
)


def _sum_numbers(values: list[Any]) -> float | None:
    numbers = [_number(value) for value in values]
    valid = [number for number in numbers if number is not None]
    return sum(valid) if valid else None


def _max_numbers(values: list[Any]) -> float | None:
    numbers = [_number(value) for value in values]
    valid = [number for number in numbers if number is not None]
    return max(valid) if valid else None


def _history_values(metrics: dict[str, Any]) -> tuple[float | None, ...]:
    gpus = metrics.get("gpus") or []
    memory_total = _number(metrics.get("memory_total_bytes"))
    memory_free = _number(metrics.get("memory_free_bytes"))
    disk_total = _number(metrics.get("disk_total_bytes"))
    disk_free = _number(metrics.get("disk_free_bytes"))
    gpu_memory_used = _sum_numbers([gpu.get("memory_used") for gpu in gpus])
    gpu_memory_total = _sum_numbers([gpu.get("memory_total") for gpu in gpus])
    return (
        _number(metrics.get("cpu_percent")),
        memory_total - memory_free if memory_total is not None and memory_free is not None else None,
        memory_total,
        disk_total - disk_free if disk_total is not None and disk_free is not None else None,
        disk_total,
        _number(metrics.get("network_rx_bps")),
        _number(metrics.get("network_tx_bps")),
        _max_numbers([gpu.get("utilization_gpu") for gpu in gpus]),
        gpu_memory_used * 1024 ** 2 if gpu_memory_used is not None else None,
        gpu_memory_total * 1024 ** 2 if gpu_memory_total is not None else None,
        _max_numbers([gpu.get("temperature") for gpu in gpus]),
        _sum_numbers([gpu.get("power_draw") for gpu in gpus]),
    )


def record_history(path: Path, metrics: dict[str, Any], collected_at: float,
                   retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
    """Persist one successful sample and prune expired raw samples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    definitions = ",\n                ".join(f"{column} REAL" for column in HISTORY_COLUMNS)
    placeholders = ", ".join("?" for _ in range(len(HISTORY_COLUMNS) + 1))
    columns = ", ".join(("collected_at",) + HISTORY_COLUMNS)
    with closing(sqlite3.connect(str(path), timeout=5)) as database:
        with database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute(f"""
                CREATE TABLE IF NOT EXISTS samples (
                    collected_at REAL PRIMARY KEY,
                    {definitions}
                )
            """)
            database.execute(
                f"INSERT OR REPLACE INTO samples ({columns}) VALUES ({placeholders})",
                (collected_at,) + _history_values(metrics),
            )
            database.execute("DELETE FROM samples WHERE collected_at < ?", (collected_at - retention_days * 86400,))


def _query_report_range(database: sqlite3.Connection, now: float, seconds: int,
                        bucket_seconds: int) -> dict[str, Any]:
    cutoff = now - seconds
    averages = ", ".join(f"AVG({column}) AS {column}" for column in HISTORY_COLUMNS)
    bucket_expression = f"CAST(collected_at / {int(bucket_seconds)} AS INTEGER) * {int(bucket_seconds)}"
    rows = database.execute(
        f"SELECT {bucket_expression} AS bucket_at, {averages} FROM samples "
        f"WHERE collected_at >= ? GROUP BY {bucket_expression} ORDER BY bucket_at",
        (cutoff,),
    ).fetchall()
    series = {
        "timestamps": [row[0] for row in rows],
        **{column: [row[index + 1] for row in rows] for index, column in enumerate(HISTORY_COLUMNS)},
    }
    statistic_fields = ", ".join(
        expression
        for column in HISTORY_COLUMNS
        for expression in (f"AVG({column})", f"MIN({column})", f"MAX({column})")
    )
    statistic_values = database.execute(
        f"SELECT {statistic_fields} FROM samples WHERE collected_at >= ?", (cutoff,)
    ).fetchone()
    stats = {
        column: {
            "avg": statistic_values[index * 3],
            "min": statistic_values[index * 3 + 1],
            "max": statistic_values[index * 3 + 2],
        }
        for index, column in enumerate(HISTORY_COLUMNS)
    }
    count, first_at, last_at = database.execute(
        "SELECT COUNT(*), MIN(collected_at), MAX(collected_at) FROM samples WHERE collected_at >= ?",
        (cutoff,),
    ).fetchone()
    return {
        "series": series,
        "stats": stats,
        "count": count,
        "first_at": first_at,
        "last_at": last_at,
        # The browser uses this to leave gaps when whole collection buckets are missing.
        "bucket_seconds": bucket_seconds,
    }


REPORT_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WinBarMonitor 实时概览与历史统计</title>
<style>
:root{color-scheme:dark;--bg:#080d18;--muted:#b8c5da;--text:#f7faff;--line:#344563;--blue:#6bb2ff;--green:#65e5a7;--orange:#ffc36b;--pink:#f38bc8}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#18264a 0,transparent 38%),var(--bg);color:var(--text);font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1180px;margin:auto;padding:32px 22px 48px}header{display:flex;gap:18px;align-items:end;justify-content:space-between;margin-bottom:24px}h1{font-size:28px;margin:0 0 6px}.sub{color:var(--muted)}h2.section-title{font-size:17px;margin:28px 0 12px}.hint{color:var(--muted);font-size:12px;margin:-5px 0 13px}
.tabs{display:flex;gap:8px}.tabs button{border:1px solid var(--line);background:#10182a;color:var(--muted);border-radius:10px;padding:8px 14px;cursor:pointer}.tabs button.active{background:#284f85;color:#fff;border-color:#4b85c7}
.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:18px}.card,.chart{background:linear-gradient(145deg,#171f34,#11182a);border:1px solid var(--line);border-radius:14px;box-shadow:0 12px 35px #0004}.card{padding:16px}.label{color:var(--muted);font-size:12px}.value{font-size:23px;font-weight:650;margin:7px 0}.detail{color:var(--muted);font-size:12px}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:14px}.chart{padding:16px}.chart h2{font-size:15px;margin:0 0 12px}.canvas-wrap{height:245px;position:relative}canvas{width:100%;height:100%;cursor:crosshair}.tooltip{position:absolute;display:none;z-index:2;pointer-events:none;min-width:170px;padding:10px 12px;border:1px solid #405170;border-radius:9px;background:#090e1beF;box-shadow:0 8px 24px #0008;color:var(--text);font-size:12px;line-height:1.5}.tooltip-time{margin-bottom:5px;color:#c9d7ef;font-weight:600}.tooltip-row{display:flex;align-items:center;justify-content:space-between;gap:18px}.tooltip-label{display:flex;align-items:center;gap:6px;color:var(--muted)}.tooltip-dot{width:7px;height:7px;border-radius:50%}
footer{color:var(--muted);margin-top:20px;font-size:12px}@media(max-width:850px){header{align-items:start;flex-direction:column}.cards{grid-template-columns:repeat(2,1fr)}.charts{grid-template-columns:1fr}}@media(max-width:480px){.cards{grid-template-columns:1fr}}
</style>
</head>
<body><main>
<header><div><h1>WinBarMonitor 实时概览</h1><div class="sub" id="subtitle"></div></div></header>
<section class="cards" id="current-cards"></section>
<h2 class="section-title">历史趋势</h2><div class="hint">移动鼠标到曲线节点，可查看该时间点的数值。</div>
<div class="tabs"><button data-range="twoHours" class="active">2 小时</button><button data-range="day">24 小时</button><button data-range="week">7 天</button><button data-range="month">30 天</button></div>
<section class="cards" id="cards"></section>
<section class="charts">
<div class="chart"><h2>CPU / GPU 利用率</h2><div class="canvas-wrap"><canvas id="util"></canvas></div></div>
<div class="chart"><h2>内存 / 显存占用</h2><div class="canvas-wrap"><canvas id="memory"></canvas></div></div>
<div class="chart"><h2>GPU 温度 / 功耗</h2><div class="canvas-wrap"><canvas id="thermal"></canvas></div></div>
<div class="chart"><h2>网络速率</h2><div class="canvas-wrap"><canvas id="network"></canvas></div></div>
</section><footer id="footer"></footer>
</main>
<script>
const DATA=__REPORT_DATA__;
const colors=['#5aa7ff','#54d69b','#ffb454','#ed75b8'];
const finite=v=>Number.isFinite(v);
const fmt=(v,d=1)=>finite(v)?v.toFixed(d):'N/A';
const gb=v=>finite(v)?v/1073741824:null;
const mbps=v=>finite(v)?v/1048576:null;
function card(label,value,detail){return `<div class="card"><div class="label">${label}</div><div class="value">${value}</div><div class="detail">${detail}</div></div>`}
function currentCards(){
 const m=DATA.current?.metrics;if(!m){document.getElementById('current-cards').innerHTML=card('实时数据','暂无','等待下一次成功采集');return}
 const memUsed=finite(m.memory_total_bytes)&&finite(m.memory_free_bytes)?m.memory_total_bytes-m.memory_free_bytes:null;
 const diskUsed=finite(m.disk_total_bytes)&&finite(m.disk_free_bytes)?m.disk_total_bytes-m.disk_free_bytes:null;
 const gpu=(m.gpus||[])[0]||{};
 const gpuMem=finite(gpu.memory_used)&&finite(gpu.memory_total)?`${fmt(gpu.memory_used/1024)} / ${fmt(gpu.memory_total/1024)} GB`:'N/A';
 document.getElementById('current-cards').innerHTML=card('主机',m.hostname||'Windows PC',DATA.current.collected_at?`更新于 ${new Date(DATA.current.collected_at*1000).toLocaleString()}`:'')+card('CPU',fmt(m.cpu_percent,0)+'%',`内存 ${fmt(gb(memUsed))} / ${fmt(gb(m.memory_total_bytes))} GB`)+card('磁盘 C:',`${fmt(gb(diskUsed))} / ${fmt(gb(m.disk_total_bytes))} GB`,'已用 / 总容量')+card('GPU',fmt(gpu.utilization_gpu,0)+'%',`显存 ${gpuMem} · ${fmt(gpu.temperature,0)}°C`)+card('网络',`↓ ${fmt(mbps(m.network_rx_bps))} MB/s`,`↑ ${fmt(mbps(m.network_tx_bps))} MB/s`);
}
function tooltipFor(box){let tip=box.querySelector('.tooltip');if(!tip){tip=document.createElement('div');tip.className='tooltip';tip.setAttribute('role','status');box.appendChild(tip)}return tip}
function draw(id,timestamps,lines,unit,fixedMax=null,hoverIndex=null,gapSeconds=0){
 const canvas=document.getElementById(id),box=canvas.parentElement,tip=tooltipFor(box),dpr=devicePixelRatio||1,w=box.clientWidth,h=box.clientHeight;
 canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);c.clearRect(0,0,w,h);
 const values=lines.flatMap(x=>x.values).filter(finite);
 if(!timestamps.length||!values.length){tip.style.display='none';c.fillStyle='#91a0ba';c.textAlign='center';c.fillText('暂无数据',w/2,h/2);return}
 const pad={l:45,r:14,t:24,b:30},cw=w-pad.l-pad.r,ch=h-pad.t-pad.b,min=0,max=fixedMax||Math.max(...values)*1.12||1;
 const first=timestamps[0],last=timestamps[timestamps.length-1]||first+1,span=Math.max(1,last-first);
 const pointX=i=>pad.l+cw*((timestamps[i]-first)/span),pointY=v=>pad.t+ch*(1-(v-min)/Math.max(1,max-min));
 c.strokeStyle='#26324b';c.fillStyle='#91a0ba';c.font='11px -apple-system';c.textAlign='right';
 for(let i=0;i<=4;i++){const y=pad.t+ch*i/4;c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();c.fillText(fmt(max*(1-i/4),max<10?1:0)+unit,pad.l-7,y+4)}
 lines.forEach((line,index)=>{c.strokeStyle=colors[index];c.lineWidth=2;c.beginPath();let started=false;line.values.forEach((v,i)=>{const previous=timestamps[i-1];const hasTimeGap=i>0&&gapSeconds>0&&timestamps[i]-previous>gapSeconds*1.5;if(!finite(v)||hasTimeGap){started=false;if(!finite(v))return}const x=pointX(i),y=pointY(v);started?c.lineTo(x,y):c.moveTo(x,y);started=true});c.stroke();line.values.forEach((v,i)=>{if(!finite(v))return;c.beginPath();c.fillStyle=colors[index];c.arc(pointX(i),pointY(v),i===hoverIndex?4:1.7,0,Math.PI*2);c.fill()});c.fillStyle=colors[index];c.textAlign='left';c.fillText(line.name,pad.l+index*110,13)});
 if(hoverIndex!==null){const x=pointX(hoverIndex);c.strokeStyle='#b9c8df99';c.lineWidth=1;c.beginPath();c.moveTo(x,pad.t);c.lineTo(x,pad.t+ch);c.stroke()}
 c.fillStyle='#91a0ba';c.textAlign='left';c.fillText(new Date(first*1000).toLocaleString(),pad.l,h-8);c.textAlign='right';c.fillText(new Date(last*1000).toLocaleString(),w-pad.r,h-8);
 canvas.onmousemove=event=>{const rect=canvas.getBoundingClientRect(),mouseX=event.clientX-rect.left;if(mouseX<pad.l||mouseX>w-pad.r){tip.style.display='none';if(hoverIndex!==null)draw(id,timestamps,lines,unit,fixedMax,null,gapSeconds);return}let nearest=null,distance=Infinity;timestamps.forEach((_,i)=>{if(!lines.some(line=>finite(line.values[i])))return;const delta=Math.abs(pointX(i)-mouseX);if(delta<distance){distance=delta;nearest=i}});if(nearest===null)return;draw(id,timestamps,lines,unit,fixedMax,nearest,gapSeconds);const rows=lines.map((line,index)=>({line,index})).filter(item=>finite(item.line.values[nearest])).map(item=>`<div class="tooltip-row"><span class="tooltip-label"><span class="tooltip-dot" style="background:${colors[item.index]}"></span>${item.line.name}</span><strong>${fmt(item.line.values[nearest],Math.abs(item.line.values[nearest])<10?2:1)}${unit}</strong></div>`).join('');tip.innerHTML=`<div class="tooltip-time">${new Date(timestamps[nearest]*1000).toLocaleString([], {hour12:false})}</div>${rows}`;tip.style.display='block';const x=pointX(nearest),left=x+14+tip.offsetWidth>w?x-tip.offsetWidth-10:x+10;tip.style.left=`${Math.max(4,left)}px`;tip.style.top=`${Math.max(26,Math.min(h-tip.offsetHeight-6,event.clientY-rect.top-12))}px`};
 canvas.onmouseleave=()=>{tip.style.display='none';if(hoverIndex!==null)draw(id,timestamps,lines,unit,fixedMax,null,gapSeconds)};
}
function render(key){
 const range=DATA.ranges[key],s=range.stats,q=range.series;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.range===key));
 const cpu=s.cpu_percent,gpu=s.gpu_percent,mem=s.memory_used_bytes,temp=s.gpu_temperature,netRx=s.network_rx_bps,netTx=s.network_tx_bps;
 document.getElementById('cards').innerHTML=card('CPU 平均',fmt(cpu.avg,0)+'%',`峰值 ${fmt(cpu.max,0)}%`)+card('GPU 平均',fmt(gpu.avg,0)+'%',`峰值 ${fmt(gpu.max,0)}%`)+card('内存平均',fmt(gb(mem.avg))+' GB',`峰值 ${fmt(gb(mem.max))} GB`)+card('GPU 最高温度',fmt(temp.max,0)+'°C',`平均 ${fmt(temp.avg,0)}°C`)+card('网络平均',`↓ ${fmt(mbps(netRx.avg))} MB/s`,`↑ ${fmt(mbps(netTx.avg))} MB/s`);
 document.getElementById('subtitle').textContent=`${range.count} 个有效样本 · 最后采集 ${range.last_at?new Date(range.last_at*1000).toLocaleString():'暂无'}`;
 draw('util',q.timestamps,[{name:'CPU',values:q.cpu_percent},{name:'GPU（最高）',values:q.gpu_percent}],'%',100,null,range.bucket_seconds);
 draw('memory',q.timestamps,[{name:'内存 GB',values:q.memory_used_bytes.map(gb)},{name:'显存合计 GB',values:q.gpu_memory_used_bytes.map(gb)}],' GB',null,null,range.bucket_seconds);
 draw('thermal',q.timestamps,[{name:'最高温度 °C',values:q.gpu_temperature},{name:'功耗合计 W',values:q.gpu_power_watts}],'',null,null,range.bucket_seconds);
 draw('network',q.timestamps,[{name:'下载 MB/s',values:q.network_rx_bps.map(mbps)},{name:'上传 MB/s',values:q.network_tx_bps.map(mbps)}],' MB/s',null,null,range.bucket_seconds);
}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>render(b.dataset.range));
window.onresize=()=>render(document.querySelector('.tabs button.active').dataset.range);
document.getElementById('footer').textContent=`报告生成于 ${new Date(DATA.generated_at*1000).toLocaleString()} · 数据仅保存在本机`;
currentCards();
render('twoHours');
</script></body></html>'''


def write_history_report(history_path: Path, report_path: Path, now: float,
                         metrics: dict[str, Any] | None = None) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(history_path), timeout=5)) as database:
        payload = {
            "generated_at": now,
            "current": {"collected_at": now, "metrics": metrics} if metrics else None,
            "ranges": {
                "twoHours": _query_report_range(database, now, 2 * 3600, 60),
                "day": _query_report_range(database, now, 86400, 300),
                "week": _query_report_range(database, now, 7 * 86400, 3600),
                "month": _query_report_range(database, now, 30 * 86400, 21600),
            },
        }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(REPORT_TEMPLATE.replace("__REPORT_DATA__", encoded), encoding="utf-8")
    temporary.replace(report_path)


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


ICON_COLORS = {
    "green": (52, 199, 89),
    "yellow": (255, 204, 0),
    "red": (255, 59, 48),
}


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _desktop_icon_png(color: str) -> bytes:
    """Create a tiny RGBA desktop-monitor PNG without external dependencies."""
    width = height = 18
    red, green, blue = ICON_COLORS[color]
    pixels = bytearray(width * height * 4)

    def paint(x: int, y: int, alpha: int = 255) -> None:
        offset = (y * width + x) * 4
        pixels[offset:offset + 4] = bytes((red, green, blue, alpha))

    # Rounded monitor outline with a lightly tinted screen.
    for y in range(3, 11):
        for x in range(2, 16):
            border = y in (3, 10) or x in (2, 15)
            if border and not ((x, y) in ((2, 3), (15, 3), (2, 10), (15, 10))):
                paint(x, y)
            elif not border:
                paint(x, y, 64)
    # Neck and base.
    for y in range(11, 14):
        for x in range(8, 10):
            paint(x, y)
    for y in range(14, 16):
        for x in range(5, 13):
            paint(x, y)

    scanlines = b"".join(b"\x00" + pixels[y * width * 4:(y + 1) * width * 4] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", zlib.compress(scanlines, 9)) + _png_chunk(b"IEND", b"")


def _menu_icon(color: str) -> str:
    """Render a color-preserving PNG as an icon-only SwiftBar title."""
    encoded = base64.b64encode(_desktop_icon_png(color)).decode("ascii")
    return f"\u200b | image={encoded} dropdown=false"


def _history_action(report_path: Path) -> str:
    quoted_path = json.dumps(str(report_path), ensure_ascii=False)
    return f"📊 查看历史统计 | bash=/usr/bin/open param1={quoted_path} terminal=false"


def _menu_bar_line(metrics: dict[str, Any], mode: str, *, stale: bool = False) -> str:
    """Return the SwiftBar title line for the selected, compact metric."""
    gpu = (metrics.get("gpus") or [{}])[0]
    gpu_util = _percent(gpu.get("utilization_gpu"))
    temperature = _number(gpu.get("temperature"))
    power_draw = _number(gpu.get("power_draw"))
    values = {
        "gpu": f"GPU {gpu_util}",
        "cpu": f"CPU {_percent(metrics.get('cpu_percent'))}",
        "vram": "显存 " + (
            f"{_fmt_bytes(gpu.get('memory_used') * 1024 ** 2)} / {_fmt_bytes(gpu.get('memory_total') * 1024 ** 2)}"
            if gpu.get("memory_used") is not None and gpu.get("memory_total") is not None else "N/A"),
        "temperature": f"GPU {temperature:.0f}°C" if temperature is not None else "GPU N/A",
        "power": f"GPU {power_draw:.0f} W" if power_draw is not None else "GPU N/A",
        "network": f"↓ {_fmt_rate(metrics.get('network_rx_bps'))}",
    }
    selected = "gpu" if mode == "auto" and gpu.get("utilization_gpu") is not None else ("cpu" if mode == "auto" else mode)
    return f"{values[selected]}{_menu_icon('yellow' if stale else 'green')}"


def _display_mode_action(label: str, mode: str, settings_path: Path, *, selected: bool) -> str:
    script = json.dumps(str(Path(__file__).resolve()), ensure_ascii=False)
    settings = json.dumps(str(settings_path), ensure_ascii=False)
    prefix = "✓ " if selected else ""
    return (f"{prefix}{label} | bash=/usr/bin/env param1=python3 param2={script} "
            f"param3=--set-menu-bar-mode param4={mode} param5=--settings-path param6={settings} terminal=false refresh=true")


def _display_settings_menu(settings_path: Path, current_mode: str) -> list[str]:
    return ["⚙️ 菜单栏显示设置", *[
        "--" + _display_mode_action(label, mode, settings_path, selected=mode == current_mode)
        for mode, label in MENU_BAR_MODE_LABELS.items()
    ]]


def _settings_action(command: str, settings_path: Path, *arguments: str) -> str:
    script = json.dumps(str(Path(__file__).resolve()), ensure_ascii=False)
    settings = json.dumps(str(settings_path), ensure_ascii=False)
    parameters = ["bash=/usr/bin/env", "param1=python3", f"param2={script}", f"param3={command}"]
    parameters.extend(f"param{index}={argument}" for index, argument in enumerate(arguments, start=4))
    settings_index = len(parameters) + 1
    parameters.extend([f"param{settings_index}=--settings-path", f"param{settings_index + 1}={settings}",
                       "terminal=false", "refresh=true"])
    return " | " + " ".join(parameters)


def _fmt_duration(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"{seconds // 60} 分钟"
    if seconds < 60:
        return f"{seconds} 秒"
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes} 分钟 {remainder} 秒"


def _set_low_usage_alert_action(alert: LowUsageAlertConfig, settings_path: Path) -> str:
    return _settings_action(
        "--set-low-usage-alert", settings_path,
        "true" if alert.enabled else "false",
        f"{alert.gpu_utilization_threshold:g}",
        f"{alert.vram_utilization_threshold:g}",
        str(alert.duration_seconds),
    )


ALERT_PRESETS = (
    ("GPU ≤ 10% · 显存 ≤ 10% · 20 分钟", 10, 10, 20 * 60),
    ("GPU ≤ 5% · 显存 ≤ 5% · 10 分钟", 5, 5, 10 * 60),
    ("GPU ≤ 5% · 显存 ≤ 10% · 5 分钟", 5, 10, 5 * 60),
    ("GPU ≤ 2% · 显存 ≤ 5% · 2 分钟", 2, 5, 2 * 60),
)


def _low_usage_alert_menu(alert: LowUsageAlertConfig, status: LowUsageAlertStatus,
                          settings_path: Path, collected_at: float | None) -> list[str]:
    threshold_text = (f"GPU ≤ {alert.gpu_utilization_threshold:g}% · "
                      f"显存 ≤ {alert.vram_utilization_threshold:g}% · {_fmt_duration(alert.duration_seconds)}")
    # Keep this hierarchy and every child count stable across refreshes.  Recent
    # SwiftBar versions can leave a reused parent submenu disabled when its
    # child count changes after an action (for example, enable -> disable).
    if not alert.enabled:
        status_text = "未启用"
        action_label = "--启用当前规则"
    elif status.notified:
        status_text = "已提醒 · GPU 再次繁忙后将重新布防"
        action_label = "--关闭提醒"
    elif status.low_since is not None:
        reference = collected_at if collected_at is not None else time.time()
        elapsed = max(0, int(reference - status.low_since))
        status_text = f"低占用计时中 · {_fmt_duration(elapsed)} / {_fmt_duration(alert.duration_seconds)}"
        action_label = "--关闭提醒"
    elif status.max_gpu_utilization is not None and status.vram_utilization is not None:
        status_text = f"监测中 · GPU {status.max_gpu_utilization:.0f}% · 显存 {status.vram_utilization:.0f}%"
        action_label = "--关闭提醒"
    else:
        status_text = "监测中 · 等待完整 GPU 数据"
        action_label = "--关闭提醒"
    lines = ["⏰ 任务完成提醒", f"--状态 · {status_text}", f"--当前规则 · {threshold_text}"]
    lines.append(action_label + _set_low_usage_alert_action(replace(alert, enabled=not alert.enabled), settings_path))
    lines.extend([
        "--快速预设",
        *[
            "----" + label + _set_low_usage_alert_action(
                LowUsageAlertConfig(True, gpu, vram, duration), settings_path)
            for label, gpu, vram, duration in ALERT_PRESETS
        ],
        "--输入自定义规则并启用…" + _settings_action("--configure-low-usage-alert", settings_path),
    ])
    return lines


def _details_action(label: str, report_path: Path) -> str:
    """Make a read-only metric a high-contrast, meaningful SwiftBar menu item."""
    quoted_path = json.dumps(str(report_path), ensure_ascii=False)
    return f"{label} | bash=/usr/bin/open param1={quoted_path} terminal=false"


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
           collected_at: float | None = None, report_path: Path = DEFAULT_REPORT_PATH,
           ssh_alias: str | None = None, plugin_name: str = "winbar.1m.py",
           menu_bar_mode: str = "auto", settings_path: Path = DEFAULT_SETTINGS_PATH,
           low_usage_alert: LowUsageAlertConfig = LowUsageAlertConfig(),
           low_usage_alert_status: LowUsageAlertStatus = LowUsageAlertStatus()) -> str:
    gpu = (metrics.get("gpus") or [{}])[0]
    cpu = _percent(metrics.get("cpu_percent"))
    gpu_util = _percent(gpu.get("utilization_gpu"))
    temperature = gpu.get("temperature")
    power_draw = gpu.get("power_draw")
    temperature_text = f"{temperature:.0f}°C" if temperature is not None else "N/A"
    power_text = f"{power_draw:.0f} W" if power_draw is not None else "N/A"
    state = "🟡 缓存" if stale else "🟢 在线"
    icon_color = "yellow" if stale else "green"
    summary = f"{state} · GPU {gpu_util} · CPU {cpu}"
    hostname = str(metrics.get("hostname") or "").strip()
    alias = (ssh_alias if ssh_alias is not None else Config.from_env().ssh_alias).strip()
    host_label = hostname or alias or "Windows PC"
    detail = lambda label: _details_action(label, report_path)
    lines = [_menu_bar_line(metrics, menu_bar_mode, stale=stale), "---", detail(summary), "---",
             detail("ⓘ 点击任一指标查看实时详情与历史趋势"),
             detail(f"🖥️ {host_label}"), detail(f"CPU 占用 · {cpu}"),
             detail(f"内存 · {_fmt_bytes(metrics.get('memory_total_bytes') - metrics.get('memory_free_bytes')) if metrics.get('memory_total_bytes') is not None and metrics.get('memory_free_bytes') is not None else 'N/A'} / {_fmt_bytes(metrics.get('memory_total_bytes'))}"),
             detail(f"C盘 · {_fmt_bytes(metrics.get('disk_total_bytes') - metrics.get('disk_free_bytes')) if metrics.get('disk_total_bytes') is not None and metrics.get('disk_free_bytes') is not None else 'N/A'} / {_fmt_bytes(metrics.get('disk_total_bytes'))}"),
             detail(f"网络 · ↓ {_fmt_rate(metrics.get('network_rx_bps'))} · ↑ {_fmt_rate(metrics.get('network_tx_bps'))}"),
             detail(f"运行时间 · {_uptime(metrics.get('last_boot'))}")]
    for index, gpu in enumerate(metrics.get("gpus") or []):
        gpu_util = _percent(gpu.get("utilization_gpu"))
        temperature = gpu.get("temperature")
        power_draw = gpu.get("power_draw")
        temperature_text = f"{temperature:.0f}°C" if temperature is not None else "N/A"
        power_text = f"{power_draw:.0f} W" if power_draw is not None else "N/A"
        lines.extend(["---", detail(f"🎮 GPU {index + 1} · {gpu.get('name', 'NVIDIA GPU')}"),
                      detail(f"GPU 利用率 · {gpu_util}"),
                      detail(f"显存 · {_fmt_bytes(gpu.get('memory_used') * 1024 ** 2) if gpu.get('memory_used') is not None else 'N/A'} / {_fmt_bytes(gpu.get('memory_total') * 1024 ** 2) if gpu.get('memory_total') is not None else 'N/A'}"),
                      detail(f"温度 · {temperature_text}"), detail(f"功耗 · {power_text}")])
    lines.extend(["---", detail("🔥 Top 进程")])
    processes = metrics.get("processes") or []
    for proc in processes[:5]:
        name = str(proc.get("name") or "?")[:24]
        lines.append(detail(f"{name} · CPU {_percent(proc.get('cpu_percent'))} · PID {proc.get('pid', 'N/A')}"))
    if error:
        lines.extend(["---", detail(f"⚠️ {error}")])
    if collected_at:
        timestamp = datetime.fromtimestamp(collected_at).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"---\n{detail(f'🕒 {timestamp}')}")
    lines.extend(["---", *_low_usage_alert_menu(low_usage_alert, low_usage_alert_status, settings_path, collected_at),
                  "---", *_display_settings_menu(settings_path, menu_bar_mode), "---", _history_action(report_path), "🔄 手动刷新 | refresh=true",
                  f"🔐 打开 SSH | bash=/usr/bin/ssh param1={alias} terminal=true"])
    return "\n".join(lines)


def main() -> int:
    config = Config.from_env()
    cached, cached_at = read_cache(config.cache_path)
    try:
        metrics = collect_remote(config)
        collected_at = time.time()
        write_cache(config.cache_path, metrics, collected_at)
        history_error = None
        try:
            record_history(config.history_path, metrics, collected_at, config.retention_days)
            write_history_report(config.history_path, config.report_path, collected_at, metrics)
        except (OSError, ValueError, sqlite3.Error) as exc:
            history_error = f"历史记录失败：{exc}"
        alert_status = LowUsageAlertStatus()
        alert_error = None
        try:
            alert_status, should_notify = update_low_usage_alert(
                config.alert_state_path, config.low_usage_alert, metrics, collected_at)
            if should_notify:
                send_low_usage_notification(
                    str(metrics.get("hostname") or config.ssh_alias), config.low_usage_alert, alert_status)
                mark_low_usage_alert_notified(config.alert_state_path, config.low_usage_alert)
                alert_status = replace(alert_status, notified=True)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            alert_error = f"提醒失败：{exc}"
        errors = [error for error in (history_error, alert_error) if error]
        print(render(metrics, error=" · ".join(errors) or None, collected_at=collected_at,
                     report_path=config.report_path, ssh_alias=config.ssh_alias,
                     menu_bar_mode=config.menu_bar_mode, settings_path=config.settings_path,
                     low_usage_alert=config.low_usage_alert, low_usage_alert_status=alert_status))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        if cached:
            print(render(normalize_metrics(cached), stale=True, error=f"连接失败：{exc}",
                         collected_at=cached_at, report_path=config.report_path,
                         ssh_alias=config.ssh_alias, menu_bar_mode=config.menu_bar_mode,
                         settings_path=config.settings_path, low_usage_alert=config.low_usage_alert))
        else:
            print(f"{_menu_icon('red')}\n---\n🔴 离线 · Windows\n---\n⚠️ {exc}\n---\n"
                  + "\n".join(_low_usage_alert_menu(config.low_usage_alert, LowUsageAlertStatus(),
                                                       config.settings_path, None))
                  + f"\n---\n{_history_action(config.report_path)}\n🔄 手动刷新 | refresh=true")
    return 0


def _prompt_with_osascript(prompt: str, default: str) -> str | None:
    """Show one native input dialog; cancelling leaves preferences untouched."""
    dialog = (
        f"display dialog {json.dumps(prompt, ensure_ascii=False)} "
        f"default answer {json.dumps(default, ensure_ascii=False)} "
        'buttons {"取消", "保存并启用"} default button "保存并启用" cancel button "取消" '
        'with title "WinBarMonitor 自定义任务完成提醒"'
    )
    try:
        completed = subprocess.run(["/usr/bin/osascript", "-e", f"text returned of ({dialog})"],
                                   capture_output=True, text=True, errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"无法打开设置窗口：{exc}") from None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _prompt_low_usage_alert_config(current: LowUsageAlertConfig) -> LowUsageAlertConfig | None:
    """Collect every custom value in one compact, parseable macOS dialog."""
    value = _prompt_with_osascript(
        "输入：GPU 阈值 %, 显存阈值 %, 持续时间（分钟）\n例如：5, 5, 10",
        f"{current.gpu_utilization_threshold:g}, {current.vram_utilization_threshold:g}, "
        f"{current.duration_seconds / 60:g}",
    )
    if value is None:
        return None
    fields = [field.strip() for field in re.split(r"[,，]", value)]
    if len(fields) != 3 or any(not field for field in fields):
        raise ValueError("请按“GPU 阈值, 显存阈值, 持续分钟”格式输入，例如：5, 5, 10")
    gpu = _number(fields[0].rstrip("% "))
    vram = _number(fields[1].rstrip("% "))
    duration_minutes = _number(fields[2])
    if gpu is None or not 0 <= gpu <= 100:
        raise ValueError("GPU 利用率阈值必须是 0 到 100 之间的数字")
    if vram is None or not 0 <= vram <= 100:
        raise ValueError("显存占用阈值必须是 0 到 100 之间的数字")
    if duration_minutes is None or duration_minutes <= 0:
        raise ValueError("持续时间必须是大于 0 的分钟数")
    return LowUsageAlertConfig(
        enabled=True,
        gpu_utilization_threshold=gpu,
        vram_utilization_threshold=vram,
        duration_seconds=max(1, round(duration_minutes * 60)),
    )


def _settings_path_from_arguments(arguments: list[str], start: int) -> Path:
    if len(arguments) == start:
        return DEFAULT_SETTINGS_PATH
    if len(arguments) == start + 2 and arguments[start] == "--settings-path":
        return Path(arguments[start + 1]).expanduser()
    raise ValueError("设置路径参数无效")


def _low_usage_alert_from_cli(values: list[str]) -> LowUsageAlertConfig:
    if len(values) != 4 or values[0].lower() not in ("true", "false"):
        raise ValueError("提醒设置格式无效")
    gpu = _number(values[1])
    vram = _number(values[2])
    duration = _number(values[3])
    if gpu is None or not 0 <= gpu <= 100:
        raise ValueError("GPU 利用率阈值必须是 0 到 100 之间的数字")
    if vram is None or not 0 <= vram <= 100:
        raise ValueError("显存占用阈值必须是 0 到 100 之间的数字")
    if duration is None or duration < 60:
        raise ValueError("持续时间至少为 1 分钟")
    return LowUsageAlertConfig(
        enabled=values[0].lower() == "true",
        gpu_utilization_threshold=gpu,
        vram_utilization_threshold=vram,
        duration_seconds=round(duration),
    )


def cli_main(arguments: list[str]) -> int:
    """Handle local-only SwiftBar settings commands."""
    if not arguments:
        return main()
    try:
        command = arguments[0]
        if command == "--set-menu-bar-mode":
            if len(arguments) not in (2, 4):
                raise ValueError("用法：--set-menu-bar-mode <模式> [--settings-path <路径>]")
            save_menu_bar_mode(_settings_path_from_arguments(arguments, 2), arguments[1])
        elif command == "--set-low-usage-alert":
            if len(arguments) not in (5, 7):
                raise ValueError("用法：--set-low-usage-alert <true|false> <GPU%> <显存%> <秒> [--settings-path <路径>]")
            settings_path = _settings_path_from_arguments(arguments, 5)
            save_low_usage_alert_config(settings_path, _low_usage_alert_from_cli(arguments[1:5]))
        elif command == "--set-low-usage-alert-enabled":
            if len(arguments) not in (2, 4):
                raise ValueError("用法：--set-low-usage-alert-enabled <true|false> [--settings-path <路径>]")
            enabled_text = arguments[1].strip().lower()
            if enabled_text not in ("true", "false"):
                raise ValueError("提醒开关只能是 true 或 false")
            settings_path = _settings_path_from_arguments(arguments, 2)
            current = _low_usage_alert_config(_read_settings(settings_path).get("low_usage_alert"))
            save_low_usage_alert_config(settings_path, replace(current, enabled=enabled_text == "true"))
        elif command == "--configure-low-usage-alert":
            settings_path = _settings_path_from_arguments(arguments, 1)
            current = _low_usage_alert_config(_read_settings(settings_path).get("low_usage_alert"))
            updated = _prompt_low_usage_alert_config(current)
            if updated is None:
                return 0
            save_low_usage_alert_config(settings_path, updated)
        else:
            raise ValueError("用法：--set-menu-bar-mode <模式> [--settings-path <路径>]；"
                             "--configure-low-usage-alert [--settings-path <路径>]")
        _refresh_installed_plugin()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"保存设置失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main(sys.argv[1:]))
