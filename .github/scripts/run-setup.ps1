<#
.SYNOPSIS
  Runs the SPC installer (or uninstaller) unattended for the Windows test: waits at most -TimeoutMinutes, then prints the setup log,
  the processes that were still running and stops the tree, so that a hanging step shows where it hangs instead of holding the job.
#>
param(
  [Parameter(Mandatory = $true)][string]$Exe,
  [Parameter(Mandatory = $true)][string]$Log,
  [string[]]$Arguments = @(),
  [int]$TimeoutMinutes = 15
)
$ErrorActionPreference = "Stop"
$all = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$Log`"") + $Arguments
$p = Start-Process $Exe -ArgumentList $all -PassThru
if (-not $p.WaitForExit($TimeoutMinutes * 60 * 1000)) {
  Write-Host "::error::setup did not end within $TimeoutMinutes minutes"
  if (Test-Path $Log) { Get-Content $Log -Tail 80 }
  Get-CimInstance Win32_Process | Where-Object { $_.Name -match "python|setup|is-|powershell|schtasks|unins" } |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine | Format-Table -AutoSize -Wrap
  taskkill /T /F /PID $p.Id | Out-Null
  throw "setup timed out"
}
if (Test-Path $Log) { Get-Content $Log | Select-String "SPC:" | ForEach-Object { $_.Line } }
if ($p.ExitCode -ne 0) {
  if (Test-Path $Log) { Get-Content $Log -Tail 40 }
  throw "setup ended with code $($p.ExitCode)"
}
