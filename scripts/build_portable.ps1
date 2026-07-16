Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -e ".[packaging]"
& ".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm IbayRentalDashboard.spec

$DistRoot = Join-Path $ProjectRoot "dist\IbayRentalDashboard"
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "data\raw\ibay") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "data\imports\schema_aligned") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "data\processed") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "data\processed\imports") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "data\review") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DistRoot "reports") | Out-Null

$SeedDataset = Join-Path $ProjectRoot "data\processed\ibay_rentals_master.csv.gz"
if (Test-Path $SeedDataset) {
    Copy-Item -Force $SeedDataset (Join-Path $DistRoot "data\processed\ibay_rentals_master.csv.gz")
}

$SchemaImportRoot = Join-Path $ProjectRoot "data\imports\schema_aligned"
if (Test-Path $SchemaImportRoot) {
    Copy-Item -Force -Recurse (Join-Path $SchemaImportRoot "*") (Join-Path $DistRoot "data\imports\schema_aligned")
}

Write-Host "Portable build created at: $DistRoot"
Write-Host "Run: $DistRoot\IbayRentalDashboard.exe"
