<#
Collect one local Windows performance sample for WinBarMonitor.

The script has no network client and no third-party dependency.  It writes a
small atomic latest.json snapshot plus a UTC-day JSONL history file.  It can be
run manually or by run-collector.ps1.
#>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$DataDirectory = (Join-Path $env:ProgramData 'WinBarMonitor'),

    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Write-AtomicUtf8File {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Content)

    $parent = [System.IO.Path]::GetDirectoryName($Path)
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    $temporary = Join-Path $parent ('.{0}.{1}.tmp' -f [System.IO.Path]::GetFileName($Path),
        [System.Guid]::NewGuid().ToString('N'))
    [System.IO.File]::WriteAllText($temporary, $Content, $Utf8NoBom)
    $backup = Join-Path $parent ('.{0}.{1}.bak' -f [System.IO.Path]::GetFileName($Path),
        [System.Guid]::NewGuid().ToString('N'))
    $lastError = $null
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        try {
            if ([System.IO.File]::Exists($Path)) {
                [System.IO.File]::Replace($temporary, $Path, $backup, $true)
            } else {
                [System.IO.File]::Move($temporary, $Path)
            }
            if ([System.IO.File]::Exists($backup)) {
                Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
            }
            return
        } catch {
            # A remote read may hold the old snapshot briefly without delete
            # sharing.  Retrying keeps an atomic replacement rather than
            # exposing a partially written file.
            $lastError = $_
            Start-Sleep -Milliseconds (50 * ($attempt + 1))
        }
    }
    if ([System.IO.File]::Exists($temporary)) {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
    if ([System.IO.File]::Exists($backup)) {
        Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    }
    throw $lastError
}

function Add-Utf8Line {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Line)

    $parent = [System.IO.Path]::GetDirectoryName($Path)
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try {
        $writer = [System.IO.StreamWriter]::new($stream, $Utf8NoBom)
        try {
            $writer.WriteLine($Line)
        } finally {
            $writer.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Write-CollectorError {
    param([Parameter(Mandatory)][string]$Message)

    try {
        [System.IO.Directory]::CreateDirectory($DataDirectory) | Out-Null
        $path = Join-Path $DataDirectory 'collector-errors.log'
        if ([System.IO.File]::Exists($path) -and (Get-Item -LiteralPath $path).Length -gt 524288) {
            Move-Item -LiteralPath $path -Destination "$path.1" -Force
        }
        Add-Utf8Line -Path $path -Line ("{0} {1}" -f
            [System.DateTimeOffset]::UtcNow.ToString('o'), $Message)
    } catch {
        # Preserve the original collection error if its diagnostic log cannot
        # be written (for example, because the disk is full).
    }
}

function Find-NvidiaSmi {
    $candidates = @()
    if ($env:ProgramW6432) {
        $candidates += (Join-Path $env:ProgramW6432 'NVIDIA Corporation\NVSMI\nvidia-smi.exe')
    }
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe')
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ([System.IO.File]::Exists($candidate)) { return $candidate }
    }
    $command = Get-Command 'nvidia-smi.exe' -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Get-GpuMetrics {
    $executable = Find-NvidiaSmi
    if (-not $executable) { return @() }

    $rows = @()
    try {
        $arguments = @(
            '--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw',
            '--format=csv,noheader,nounits'
        )
        $lines = @(& $executable @arguments 2>$null)
        foreach ($line in $lines) {
            $parts = @($line -split ',' | ForEach-Object { $_.Trim() })
            if ($parts.Count -ge 7) {
                $rows += [ordered]@{
                    name = $parts[0]
                    utilization_gpu = $parts[1]
                    utilization_memory = $parts[2]
                    memory_used = $parts[3]
                    memory_total = $parts[4]
                    temperature = $parts[5]
                    power_draw = $parts[6]
                }
            }
        }
    } catch {
        # A missing driver, sleeping dGPU, or unsupported metric must not stop
        # CPU/memory/disk monitoring.
    }
    return @($rows)
}

function Remove-ExpiredHistory {
    param([Parameter(Mandatory)][string]$HistoryDirectory)

    $cutoff = [System.DateTime]::UtcNow.AddDays(-$RetentionDays)
    Get-ChildItem -LiteralPath $HistoryDirectory -Filter '*.jsonl' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTimeUtc -lt $cutoff } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

try {
    $historyDirectory = Join-Path $DataDirectory 'history'
    [System.IO.Directory]::CreateDirectory($historyDirectory) | Out-Null

    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $computer = $env:COMPUTERNAME
    if (-not $computer) {
        $computer = (Get-CimInstance -ClassName Win32_ComputerSystem).Name
    }
    $cpu = Get-CimInstance -ClassName Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
    $disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='C:'"
    $networkRows = @(Get-CimInstance -ClassName Win32_PerfFormattedData_Tcpip_NetworkInterface)
    $processRows = @(Get-CimInstance -ClassName Win32_PerfFormattedData_PerfProc_Process |
        Where-Object { $_.Name -notmatch '^(Idle|_Total)$' } |
        Sort-Object -Property PercentProcessorTime -Descending |
        Select-Object -First 5)
    $gpus = @(Get-GpuMetrics)
    $networkRx = ($networkRows | Measure-Object -Property BytesReceivedPersec -Sum).Sum
    $networkTx = ($networkRows | Measure-Object -Property BytesSentPersec -Sum).Sum

    $metrics = [ordered]@{
        hostname = $computer
        cpu_percent = $cpu.PercentProcessorTime
        memory_total_bytes = ([double]$os.TotalVisibleMemorySize * 1024)
        memory_free_bytes = ([double]$os.FreePhysicalMemory * 1024)
        disk_total_bytes = $disk.Size
        disk_free_bytes = $disk.FreeSpace
        network_rx_bps = $networkRx
        network_tx_bps = $networkTx
        last_boot = $os.LastBootUpTime.ToString('o')
        processes = @($processRows | ForEach-Object {
            [ordered]@{
                name = $_.Name
                cpu_percent = $_.PercentProcessorTime
                pid = $_.IDProcess
                memory_bytes = $_.WorkingSetPrivate
            }
        })
        gpus = $gpus
    }

    $now = [System.DateTimeOffset]::UtcNow
    $collectedAt = [Math]::Round($now.ToUnixTimeMilliseconds() / 1000.0, 3)
    $snapshot = [ordered]@{
        schema_version = 1
        collected_at = $collectedAt
        collected_at_utc = $now.ToString('o')
        metrics = $metrics
    }
    Write-AtomicUtf8File -Path (Join-Path $DataDirectory 'latest.json') -Content (
        $snapshot | ConvertTo-Json -Compress -Depth 6)

    # History intentionally excludes process names/PIDs.  They are useful in
    # the current snapshot but would create an unnecessary privacy and storage
    # cost in a long-running time series.
    $historyMetrics = [ordered]@{
        hostname = $metrics.hostname
        cpu_percent = $metrics.cpu_percent
        memory_total_bytes = $metrics.memory_total_bytes
        memory_free_bytes = $metrics.memory_free_bytes
        disk_total_bytes = $metrics.disk_total_bytes
        disk_free_bytes = $metrics.disk_free_bytes
        network_rx_bps = $metrics.network_rx_bps
        network_tx_bps = $metrics.network_tx_bps
        last_boot = $metrics.last_boot
        gpus = $metrics.gpus
    }
    $historyRecord = [ordered]@{
        schema_version = 1
        collected_at = $collectedAt
        collected_at_utc = $now.ToString('o')
        metrics = $historyMetrics
    }
    Add-Utf8Line -Path (Join-Path $historyDirectory ("{0}.jsonl" -f $now.ToString('yyyy-MM-dd'))) `
        -Line ($historyRecord | ConvertTo-Json -Compress -Depth 6)
    Remove-ExpiredHistory -HistoryDirectory $historyDirectory
} catch {
    Write-CollectorError -Message $_.Exception.Message
    throw
}
