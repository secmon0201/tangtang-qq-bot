param(
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$diagnostic = Join-Path $PSScriptRoot 'diagnose_upstream_compat.py'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Missing virtual environment interpreter: $python"
}
if (-not (Test-Path -LiteralPath $diagnostic -PathType Leaf)) {
    throw "Missing diagnosis script: $diagnostic"
}

$reportDir = Join-Path $root 'reports'
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$reportPath = Join-Path $reportDir "upstream-compat-$stamp.json"

$engineArgs = @('-X', 'utf8', $diagnostic, '--root', $root, '--output', $reportPath)
$stdout = & $python @engineArgs
if ($LASTEXITCODE -ne 0) {
    throw "Upstream compatibility diagnosis failed with exit code $LASTEXITCODE"
}

$report = Get-Content -LiteralPath $reportPath -Raw -Encoding utf8 | ConvertFrom-Json
if ($Json) {
    $report | ConvertTo-Json -Depth 8
    exit 0
}
$stdout | ForEach-Object { Write-Output $_ }
Write-Output "Upstream compatibility report: $reportPath"
$report.capabilities.PSObject.Properties | ForEach-Object {
    Write-Output "  $($_.Name): $($_.Value)"
}
