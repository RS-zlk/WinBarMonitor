#Requires -RunAsAdministrator
<# Remove the Windows-local collector.  Monitoring files stay unless requested. #>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$DataDirectory = (Join-Path $env:ProgramData 'WinBarMonitor'),

    [ValidateNotNullOrEmpty()]
    [string]$TaskName = 'WinBarMonitor Collector',

    [switch]$RemoveData
)

$ErrorActionPreference = 'Stop'
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
if ($RemoveData -and (Test-Path -LiteralPath $DataDirectory)) {
    Remove-Item -LiteralPath $DataDirectory -Recurse -Force
    Write-Host "Removed task and monitoring data: $DataDirectory"
} else {
    Write-Host 'Removed scheduled task; monitoring data was kept. Use -RemoveData to delete it too.'
}
