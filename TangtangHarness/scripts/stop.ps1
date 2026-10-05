param([string]$Root = '', [switch]$Internal)
$ErrorActionPreference = 'Stop'
$harnessRoot = if ($Root) { [IO.Path]::GetFullPath($Root) } else { Split-Path -Parent $PSScriptRoot }
$python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 TangtangHarness\scripts\setup.ps1' }
if (-not $Internal) {
    & (Join-Path $PSScriptRoot 'stack.ps1') -Root $harnessRoot -Action stop-harness
    exit $LASTEXITCODE
}
Push-Location -LiteralPath $harnessRoot
try { & $python -X utf8 -c "from pathlib import Path; import json; from tangtang_harness.harness_process import stop; print(json.dumps(stop(Path.cwd()), ensure_ascii=False, indent=2))"; $resultCode = $LASTEXITCODE }
finally { Pop-Location }
if ($resultCode -ne 0) { throw "Harness 停止失败，退出码 $resultCode。" }
