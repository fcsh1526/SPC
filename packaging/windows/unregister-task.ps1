<#
.SYNOPSIS
  Stops and removes the SPC server task and its firewall rule. The data folder is not touched.
#>
param([string]$TaskName = "SPC Server")
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Remove-NetFirewallRule -DisplayName $TaskName
Write-Host "Task '$TaskName' removed. The data folder was kept."
