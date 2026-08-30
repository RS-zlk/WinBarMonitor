#Requires -RunAsAdministrator
<#
Install the Windows-local WinBarMonitor collector as a boot-time SYSTEM task.

Use -ReaderAccount to grant the Windows account used by OpenSSH read-only
access to the generated record files.  The task itself never opens a network
port and does not need the Mac to be online.
#>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$DataDirectory = (Join-Path $env:ProgramData 'WinBarMonitor'),

    [ValidateRange(15, 3600)]
    [int]$IntervalSeconds = 30,

    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30,

    [string]$ReaderAccount,

    [ValidateNotNullOrEmpty()]
    [string]$TaskName = 'WinBarMonitor Collector'
)

$ErrorActionPreference = 'Stop'
$sourceFiles = @('collect-winbar.ps1', 'run-collector.ps1')
foreach ($file in $sourceFiles) {
    if (-not [System.IO.File]::Exists((Join-Path $PSScriptRoot $file))) {
        throw "Installation package is incomplete: missing $file"
    }
}

[System.IO.Directory]::CreateDirectory($DataDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $DataDirectory 'history')) | Out-Null
foreach ($file in $sourceFiles) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $DataDirectory $file) -Force
}

if ($ReaderAccount) {
    try {
        # /T also grants access to the already-created history directory;
        # inheritance alone would only cover files created after installation.
        & icacls.exe $DataDirectory /grant "${ReaderAccount}:(OI)(CI)RX" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "icacls returned exit code $LASTEXITCODE"
        }
    } catch {
        throw "Cannot grant read-only access to ${ReaderAccount}: $($_.Exception.Message)"
    }
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$runner = Join-Path $DataDirectory 'run-collector.ps1'
$arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -DataDirectory "{1}" -IntervalSeconds {2} -RetentionDays {3}' -f `
    $runner, $DataDirectory, $IntervalSeconds, $RetentionDays
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([System.TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Collect local metrics for WinBarMonitor every $IntervalSeconds seconds." -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

Write-Host "Installed Windows local collector: $TaskName"
Write-Host "Data directory: $DataDirectory"
Write-Host "Sample interval: $IntervalSeconds seconds"
if ($ReaderAccount) {
    Write-Host "Granted read-only access to: $ReaderAccount"
} else {
    Write-Warning 'No -ReaderAccount was provided. Grant the Windows SSH account read access before using the Mac plugin.'
}
if (Test-Path -LiteralPath (Join-Path $DataDirectory 'latest.json')) {
    Write-Host 'Verification passed: latest.json was created.'
} else {
    Write-Warning 'The task started but latest.json is not available yet. Check collector-errors.log and Task Scheduler history.'
}
