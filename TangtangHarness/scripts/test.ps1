$ErrorActionPreference = 'Stop'
$harnessRoot = Split-Path -Parent $PSScriptRoot
Push-Location $harnessRoot
try {
    & '.\.venv\Scripts\python.exe' -m pytest
    if ($LASTEXITCODE -ne 0) { throw 'Harness 行为测试失败' }
    Push-Location 'frontend'
    try { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw '控制台构建失败' } } finally { Pop-Location }
} finally { Pop-Location }
