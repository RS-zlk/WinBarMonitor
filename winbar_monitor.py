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
import sqlite3
import struct
import subprocess
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_REFRESH_SECONDS = 30
DEFAULT_SSH_TIMEOUT = 15
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "winbar-monitor" / "cache.json"
DEFAULT_HISTORY_PATH = Path.home() / ".local" / "share" / "winbar-monitor" / "history.sqlite3"
DEFAULT_REPORT_PATH = Path.home() / ".local" / "share" / "winbar-monitor" / "history.html"
DEFAULT_RETENTION_DAYS = 30


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
    history_path: Path = DEFAULT_HISTORY_PATH
    report_path: Path = DEFAULT_REPORT_PATH
    retention_days: int = DEFAULT_RETENTION_DAYS

    @classmethod
    def from_env(cls) -> "Config":
        def positive_int(name: str, default: int) -> int:
            try:
                value = int(os.environ.get(name, default))
                return max(1, value)
            except (TypeError, ValueError):
                return default

        cache_path = os.environ.get("WINBAR_CACHE_PATH")
        history_path = os.environ.get("WINBAR_HISTORY_PATH")
        report_path = os.environ.get("WINBAR_REPORT_PATH")
        return cls(
            ssh_alias=os.environ.get("WINBAR_SSH_ALIAS", cls.ssh_alias),
            refresh_seconds=positive_int("WINBAR_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS),
            ssh_timeout=positive_int("WINBAR_SSH_TIMEOUT", DEFAULT_SSH_TIMEOUT),
            cache_path=Path(cache_path).expanduser() if cache_path else DEFAULT_CACHE_PATH,
            history_path=Path(history_path).expanduser() if history_path else DEFAULT_HISTORY_PATH,
            report_path=Path(report_path).expanduser() if report_path else DEFAULT_REPORT_PATH,
            retention_days=positive_int("WINBAR_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
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


HISTORY_COLUMNS = (
    "cpu_percent", "memory_used_bytes", "memory_total_bytes", "disk_used_bytes",
    "disk_total_bytes", "network_rx_bps", "network_tx_bps", "gpu_percent",
    "gpu_memory_used_bytes", "gpu_memory_total_bytes", "gpu_temperature", "gpu_power_watts",
)


def _history_values(metrics: dict[str, Any]) -> tuple[float | None, ...]:
    gpu = (metrics.get("gpus") or [{}])[0]
    memory_total = _number(metrics.get("memory_total_bytes"))
    memory_free = _number(metrics.get("memory_free_bytes"))
    disk_total = _number(metrics.get("disk_total_bytes"))
    disk_free = _number(metrics.get("disk_free_bytes"))
    gpu_memory_used = _number(gpu.get("memory_used"))
    gpu_memory_total = _number(gpu.get("memory_total"))
    return (
        _number(metrics.get("cpu_percent")),
        memory_total - memory_free if memory_total is not None and memory_free is not None else None,
        memory_total,
        disk_total - disk_free if disk_total is not None and disk_free is not None else None,
        disk_total,
        _number(metrics.get("network_rx_bps")),
        _number(metrics.get("network_tx_bps")),
        _number(gpu.get("utilization_gpu")),
        gpu_memory_used * 1024 ** 2 if gpu_memory_used is not None else None,
        gpu_memory_total * 1024 ** 2 if gpu_memory_total is not None else None,
        _number(gpu.get("temperature")),
        _number(gpu.get("power_draw")),
    )


def record_history(path: Path, metrics: dict[str, Any], collected_at: float,
                   retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
    """Persist one successful sample and prune expired raw samples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    definitions = ",\n                ".join(f"{column} REAL" for column in HISTORY_COLUMNS)
    placeholders = ", ".join("?" for _ in range(len(HISTORY_COLUMNS) + 1))
    columns = ", ".join(("collected_at",) + HISTORY_COLUMNS)
    with sqlite3.connect(str(path), timeout=5) as database:
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
    return {"series": series, "stats": stats, "count": count, "first_at": first_at, "last_at": last_at}


REPORT_TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WinBarMonitor 历史统计</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#141b2d;--muted:#91a0ba;--text:#edf3ff;--line:#26324b;--blue:#5aa7ff;--green:#54d69b;--orange:#ffb454;--pink:#ed75b8}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#18264a 0,transparent 38%),var(--bg);color:var(--text);font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1180px;margin:auto;padding:32px 22px 48px}header{display:flex;gap:18px;align-items:end;justify-content:space-between;margin-bottom:24px}h1{font-size:28px;margin:0 0 6px}.sub{color:var(--muted)}
.tabs{display:flex;gap:8px}.tabs button{border:1px solid var(--line);background:#10182a;color:var(--muted);border-radius:10px;padding:8px 14px;cursor:pointer}.tabs button.active{background:#284f85;color:#fff;border-color:#4b85c7}
.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:18px}.card,.chart{background:linear-gradient(145deg,#171f34,#11182a);border:1px solid var(--line);border-radius:14px;box-shadow:0 12px 35px #0004}.card{padding:16px}.label{color:var(--muted);font-size:12px}.value{font-size:23px;font-weight:650;margin:7px 0}.detail{color:var(--muted);font-size:12px}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:14px}.chart{padding:16px}.chart h2{font-size:15px;margin:0 0 12px}.canvas-wrap{height:245px;position:relative}canvas{width:100%;height:100%}.empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--muted)}
footer{color:var(--muted);margin-top:20px;font-size:12px}@media(max-width:850px){header{align-items:start;flex-direction:column}.cards{grid-template-columns:repeat(2,1fr)}.charts{grid-template-columns:1fr}}@media(max-width:480px){.cards{grid-template-columns:1fr}}
</style>
</head>
<body><main>
<header><div><h1>WinBarMonitor 历史统计</h1><div class="sub" id="subtitle"></div></div><div class="tabs"><button data-range="day" class="active">24 小时</button><button data-range="week">7 天</button><button data-range="month">30 天</button></div></header>
<section class="cards" id="cards"></section>
<section class="charts">
<div class="chart"><h2>CPU / GPU 利用率</h2><div class="canvas-wrap"><canvas id="util"></canvas></div></div>
<div class="chart"><h2>内存 / 显存占用</h2><div class="canvas-wrap"><canvas id="memory"></canvas></div></div>
<div class="chart"><h2>GPU 温度 / 功耗</h2><div class="canvas-wrap"><canvas id="thermal"></canvas></div></div>
<div class="chart"><h2>网络速率</h2><div class="canvas-wrap"><canvas id="network"></canvas></div></div>
</section>
<footer id="footer"></footer>
</main>
<script>
const DATA=__REPORT_DATA__;
const colors=['#5aa7ff','#54d69b','#ffb454','#ed75b8'];
const finite=v=>Number.isFinite(v);const fmt=(v,d=1)=>finite(v)?v.toFixed(d):'N/A';
const gb=v=>finite(v)?v/1073741824:null;const mbps=v=>finite(v)?v/1048576:null;
function card(label,value,detail){return `<div class="card"><div class="label">${label}</div><div class="value">${value}</div><div class="detail">${detail}</div></div>`}
function draw(id,timestamps,lines,unit,fixedMax=null){
 const canvas=document.getElementById(id),box=canvas.parentElement,dpr=devicePixelRatio||1,w=box.clientWidth,h=box.clientHeight;canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);c.clearRect(0,0,w,h);
 const values=lines.flatMap(x=>x.values).filter(finite);if(!timestamps.length||!values.length){c.fillStyle='#91a0ba';c.textAlign='center';c.fillText('暂无数据',w/2,h/2);return}
 const pad={l:45,r:14,t:24,b:30},cw=w-pad.l-pad.r,ch=h-pad.t-pad.b,min=0,max=fixedMax||Math.max(...values)*1.12||1;
 c.strokeStyle='#26324b';c.fillStyle='#91a0ba';c.font='11px -apple-system';c.textAlign='right';for(let i=0;i<=4;i++){const y=pad.t+ch*i/4;c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();c.fillText(fmt(max*(1-i/4),max<10?1:0)+unit,pad.l-7,y+4)}
 const first=timestamps[0],last=timestamps[timestamps.length-1]||first+1;lines.forEach((line,index)=>{c.strokeStyle=colors[index];c.lineWidth=2;c.beginPath();let started=false;line.values.forEach((v,i)=>{if(!finite(v))return;const x=pad.l+cw*((timestamps[i]-first)/Math.max(1,last-first)),y=pad.t+ch*(1-(v-min)/Math.max(1,max-min));started?c.lineTo(x,y):c.moveTo(x,y);started=true});c.stroke();c.fillStyle=colors[index];c.textAlign='left';c.fillText(line.name,pad.l+index*90,13)});
 c.fillStyle='#91a0ba';c.textAlign='left';c.fillText(new Date(first*1000).toLocaleString(),pad.l,h-8);c.textAlign='right';c.fillText(new Date(last*1000).toLocaleString(),w-pad.r,h-8)
}
function render(key){
 const range=DATA.ranges[key],s=range.stats,q=range.series;document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('active',b.dataset.range===key));
 const cpu=s.cpu_percent,gpu=s.gpu_percent,mem=s.memory_used_bytes,temp=s.gpu_temperature,netRx=s.network_rx_bps,netTx=s.network_tx_bps;
 document.getElementById('cards').innerHTML=card('CPU 平均',fmt(cpu.avg,0)+'%',`峰值 ${fmt(cpu.max,0)}%`)+card('GPU 平均',fmt(gpu.avg,0)+'%',`峰值 ${fmt(gpu.max,0)}%`)+card('内存平均',fmt(gb(mem.avg))+' GB',`峰值 ${fmt(gb(mem.max))} GB`)+card('GPU 最高温度',fmt(temp.max,0)+'°C',`平均 ${fmt(temp.avg,0)}°C`)+card('网络平均',`↓ ${fmt(mbps(netRx.avg))} MB/s`,`↑ ${fmt(mbps(netTx.avg))} MB/s`);
 document.getElementById('subtitle').textContent=`${range.count} 个有效样本 · 最后采集 ${range.last_at?new Date(range.last_at*1000).toLocaleString():'暂无'}`;
 draw('util',q.timestamps,[{name:'CPU',values:q.cpu_percent},{name:'GPU',values:q.gpu_percent}],'%',100);
 draw('memory',q.timestamps,[{name:'内存 GB',values:q.memory_used_bytes.map(gb)},{name:'显存 GB',values:q.gpu_memory_used_bytes.map(gb)}],' GB');
 draw('thermal',q.timestamps,[{name:'温度 °C',values:q.gpu_temperature},{name:'功耗 W',values:q.gpu_power_watts}],'');
 draw('network',q.timestamps,[{name:'下载 MB/s',values:q.network_rx_bps.map(mbps)},{name:'上传 MB/s',values:q.network_tx_bps.map(mbps)}],' MB/s');
}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>render(b.dataset.range));window.onresize=()=>render(document.querySelector('.tabs button.active').dataset.range);document.getElementById('footer').textContent=`报告生成于 ${new Date(DATA.generated_at*1000).toLocaleString()} · 数据仅保存在本机`;render('day');
</script></body></html>'''


def write_history_report(history_path: Path, report_path: Path, now: float) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(history_path), timeout=5) as database:
        payload = {
            "generated_at": now,
            "ranges": {
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
           plugin_name: str = "winbar.1m.py") -> str:
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
    lines = [_menu_icon(icon_color), "---", summary, "---", f"🖥️ Windows · {metrics.get('hostname', 'y9000p')}",
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
    lines.extend(["---", _history_action(report_path), "🔄 手动刷新 | refresh=true",
                  f"🔐 打开 SSH | bash=/usr/bin/ssh param1={os.environ.get('WINBAR_SSH_ALIAS', 'y9000p-remote')} terminal=true"])
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
            write_history_report(config.history_path, config.report_path, collected_at)
        except (OSError, ValueError, sqlite3.Error) as exc:
            history_error = f"历史记录失败：{exc}"
        print(render(metrics, error=history_error, collected_at=collected_at, report_path=config.report_path))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        if cached:
            print(render(normalize_metrics(cached), stale=True, error=f"连接失败：{exc}",
                         collected_at=cached_at, report_path=config.report_path))
        else:
            print(f"{_menu_icon('red')}\n---\n🔴 离线 · Windows\n---\n⚠️ {exc}\n---\n"
                  f"{_history_action(config.report_path)}\n🔄 手动刷新 | refresh=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
