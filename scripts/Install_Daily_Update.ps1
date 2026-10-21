param(
    [string]$DistRoot = $PSScriptRoot,
    [ValidateSet(2, 3)][int]$Pages = 3,
    [string]$TaskName = 'IbayRentalDashboard-DailyDelta'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$DistRoot = (Resolve-Path -LiteralPath $DistRoot).Path
$Runner = Join-Path $DistRoot 'Run_Daily_Update.ps1'
if (-not (Test-Path -LiteralPath $Runner)) { throw "Missing update runner: $Runner" }
if (-not (Test-Path -LiteralPath (Join-Path $DistRoot 'IbayRentalWorker.exe'))) { throw 'Missing worker executable' }
$Action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Pages {1}' -f $Runner, $Pages) -WorkingDirectory $DistRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At '02:00'
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$IsAdmin = ([Security.Principal.WindowsPrincipal]::new($Identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($IsAdmin) {
    $Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
} else {
    $Principal = New-ScheduledTaskPrincipal -UserId $Identity.Name -LogonType Interactive -RunLevel Limited
}
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'Daily 2am iBay and Property.mv delta scrape and canonical master upsert (3 pages per category by default).' -Force | Out-Null
Write-Host "Installed $TaskName at 02:00 daily (Windows host local time)."
if (-not $IsAdmin) { Write-Host 'This task runs while your account is logged in. Run this installer as Administrator on the intranet host to run under SYSTEM even when logged out.' }
