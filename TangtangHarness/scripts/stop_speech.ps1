$ErrorActionPreference = 'Stop'
$harnessRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 TangtangHarness\scripts\setup.ps1' }
$arguments = @('-X', 'utf8', '-m', 'tangtang_harness.supervisor', 'operate', '--action', 'stop-speech', '--root', $harnessRoot)
Push-Location -LiteralPath $harnessRoot
try {
    & $python @arguments
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
