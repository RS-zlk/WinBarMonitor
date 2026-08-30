<#
Keep the local collector running at a sub-minute cadence.

Windows Task Scheduler only supports a one-minute minimum for repetition
triggers.  This runner is started once at boot and sleeps between samples, so
the project can support the 30-second default without polling over the network.
#>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$DataDirectory = (Join-Path $env:ProgramData 'WinBarMonitor'),

    [ValidateRange(15, 3600)]
    [int]$IntervalSeconds = 30,

    [ValidateRange(1, 3650)]
    [int]$RetentionDays = 30
)

$ErrorActionPreference = 'Continue'
$collector = Join-Path $PSScriptRoot 'collect-winbar.ps1'
if (-not [System.IO.File]::Exists($collector)) {
    throw "Collector script not found: $collector"
}

while ($true) {
    try {
        & $collector -DataDirectory $DataDirectory -RetentionDays $RetentionDays
    } catch {
        # collect-winbar.ps1 has already written a bounded local error log.
        # Keeping this host alive makes a transient WMI/NVIDIA failure recover
        # automatically on the next interval.
    }
    Start-Sleep -Seconds $IntervalSeconds
}
