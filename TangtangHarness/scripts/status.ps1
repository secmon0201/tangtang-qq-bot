param([string]$Root = '')
$ErrorActionPreference = 'Stop'
$harnessRoot = if ($Root) { [IO.Path]::GetFullPath($Root) } else { Split-Path -Parent $PSScriptRoot }
$python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 TangtangHarness\scripts\setup.ps1' }
Push-Location -LiteralPath $harnessRoot
try { & $python -X utf8 -c "from pathlib import Path; import json; from tangtang_harness.harness_process import status; print(json.dumps(status(Path.cwd()), ensure_ascii=False, indent=2))"; $resultCode = $LASTEXITCODE }
finally { Pop-Location }
exit $resultCode
