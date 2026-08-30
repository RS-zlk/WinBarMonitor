# Windows collector

These scripts make Windows the authoritative sampler for WinBarMonitor. They
require only Windows PowerShell and Windows built-in monitoring APIs.

## Install

Open Windows PowerShell as Administrator from the repository root. Replace the
account with the one configured as `User` in the Mac SSH alias.

```powershell
.\windows\install-windows-collector.ps1 -ReaderAccount 'DESKTOP\monitor' -IntervalSeconds 30
```

The installer creates a `WinBarMonitor Collector` task that starts at boot as
`SYSTEM`, and copies trusted scripts plus records to
`C:\ProgramData\WinBarMonitor`.

| Path | Contents |
| --- | --- |
| `latest.json` | Atomically replaced current snapshot, including top processes |
| `history\YYYY-MM-DD.jsonl` | UTC-day time series without process names or PIDs |
| `collector-errors.log` | Bounded diagnostics for failed samples |

Windows Task Scheduler cannot repeat a task more often than once per minute.
To support the 30-second default, its boot trigger starts one PowerShell runner
which sleeps between local samples. It does not open a port, contact the Mac,
or use CUDA compute or GPU memory.

Collection pauses during laptop sleep and resumes after wake. This is intended:
the collector does not keep a laptop awake or force it out of a power-saving
state.

## Verify

```powershell
Get-ScheduledTask -TaskName 'WinBarMonitor Collector'
Get-Content -Raw C:\ProgramData\WinBarMonitor\latest.json
Get-Content C:\ProgramData\WinBarMonitor\collector-errors.log -Tail 20
```

The final command is optional and only produces output after a failed sample.

To confirm continuous collection rather than a stale first sample, compare the
file timestamp again after one interval:

```powershell
Get-Item C:\ProgramData\WinBarMonitor\latest.json | Select-Object LastWriteTime
Start-Sleep -Seconds 35
Get-Item C:\ProgramData\WinBarMonitor\latest.json | Select-Object LastWriteTime
```

`Running` is the expected task state: the boot-time PowerShell runner remains
alive and sleeps between samples. If the timestamp does not advance, read the
last 20 diagnostics and rerun the installer after updating the repository:

```powershell
Get-Content C:\ProgramData\WinBarMonitor\collector-errors.log -Tail 20
.\windows\install-windows-collector.ps1 -ReaderAccount 'DESKTOP\monitor' -IntervalSeconds 30
```

Reinstallation replaces the collector scripts and restarts the task while
retaining existing record files.

## Remove

```powershell
.\windows\uninstall-windows-collector.ps1
```

This removes the task and retains records. Add `-RemoveData` only when the
record directory should be deleted too.
