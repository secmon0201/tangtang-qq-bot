param([switch]$Status)
$ErrorActionPreference = 'Stop'
$harnessRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 TangtangHarness\scripts\setup.ps1' }
$operation = 'start'
if ($Status) { $operation = 'status' }
$arguments = @('-X', 'utf8', '-m', 'tangtang_harness.supervisor', 'operate', '--action', 'start-speech', '--root', $harnessRoot)
if ($Status) { $arguments = @('-X', 'utf8', '-m', 'tangtang_harness.speech_runtime', 'status', '--root', $harnessRoot) }
Push-Location -LiteralPath $harnessRoot
try {
    & $python @arguments
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
