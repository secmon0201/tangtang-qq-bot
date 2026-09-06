param(
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run: python -m venv .venv"
}
Push-Location $Root
try {
    & $Python -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { throw 'Installing manager build dependencies failed.' }
    $pyInstallerArgs = @("--noconfirm", "--clean", "QQBotManager.spec")
    if ($Clean) { Remove-Item -LiteralPath "build" -Recurse -Force -ErrorAction SilentlyContinue }
    & $Python -m PyInstaller @pyInstallerArgs
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed to build QQBotManager.exe.' }
    $output = Join-Path $Root 'dist\QQBotManager.exe'
    if (-not (Test-Path -LiteralPath $output)) { throw 'PyInstaller completed without QQBotManager.exe.' }
    Write-Output "Built: $output"
} finally {
    Pop-Location
}
