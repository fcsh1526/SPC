<#
.SYNOPSIS
  Registers the SPC server as a scheduled task that starts when Windows starts, runs as SYSTEM and is restarted when it stops. Restricts the data folder to administrators.
#>
param(
  [Parameter(Mandatory = $true)][string]$AppDir,
  [Parameter(Mandatory = $true)][string]$DataDir,
  [string]$TaskName = "SPC Server",
  [int]$Port = 0,
  [switch]$OpenFirewall
)
$ErrorActionPreference = "Stop"
$python = Join-Path $AppDir "python\python.exe"
$config = Join-Path $DataDir "spc.json"
if (-not (Test-Path $python)) { throw "Python not found: $python" }
if (-not (Test-Path $config)) { throw "Settings file not found: $config (run spc-setup first)" }

# the data folder holds password hashes and all data: administrators and the system only
& icacls $DataDir /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" | Out-Null

$action = New-ScheduledTaskAction -Execute $python -Argument "-m spc.api --config `"$config`"" -WorkingDirectory $DataDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
if ($OpenFirewall -and $Port -gt 0) {
  Remove-NetFirewallRule -DisplayName $TaskName -ErrorAction SilentlyContinue
  New-NetFirewallRule -DisplayName $TaskName -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
}
Write-Host "Task '$TaskName' registered. Start it with: Start-ScheduledTask -TaskName '$TaskName'"
