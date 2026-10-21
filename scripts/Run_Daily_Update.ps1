param([ValidateSet(2, 3)][int]$Pages = 3)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$DistRoot = $PSScriptRoot
$Worker = Join-Path $DistRoot 'IbayRentalWorker.exe'
if (-not (Test-Path -LiteralPath $Worker)) {
    throw "IbayRentalWorker.exe was not found beside this script: $DistRoot"
}
$ReportRoot = Join-Path $DistRoot 'reports'
New-Item -ItemType Directory -Force -Path $ReportRoot | Out-Null
$LogPath = Join-Path $ReportRoot ("daily_update_{0}.log" -f (Get-Date -Format 'yyyyMMddTHHmmss'))
Set-Location -LiteralPath $DistRoot
$Process = Start-Process -FilePath $Worker -ArgumentList @('--cli', 'delta', '--pages', "$Pages") -WorkingDirectory $DistRoot -WindowStyle Hidden -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.stderr" -Wait -PassThru
exit $Process.ExitCode
