param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run: python -m venv .venv"
}
Push-Location $Root
try {
    & $Python -m pip install -e ".[dev]"
    $args = @("--noconfirm", "--clean", "QQBotManager.spec")
    if ($Clean) { Remove-Item -LiteralPath "build" -Recurse -Force -ErrorAction SilentlyContinue }
    & $Python -m PyInstaller @args
    Write-Output "Built: $Root\dist\QQBotManager.exe"
} finally {
    Pop-Location
}
