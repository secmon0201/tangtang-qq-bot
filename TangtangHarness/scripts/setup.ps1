param([string]$Python = 'py', [switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$harnessRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $harnessRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    if ($Python -eq 'py') { & $Python -3.13 -m venv (Join-Path $harnessRoot '.venv') }
    else { & $Python -m venv (Join-Path $harnessRoot '.venv') }
    if ($LASTEXITCODE -ne 0) { throw '独立 Python 环境创建失败' }
}
& $venvPython -m pip install -e "$harnessRoot[dev]"
if ($LASTEXITCODE -ne 0) { throw 'Harness 依赖安装失败' }
& $venvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw '图卡浏览器安装失败' }
if (-not $SkipFrontend) {
    Push-Location (Join-Path $harnessRoot 'frontend')
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw '控制台依赖安装失败' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw '控制台构建失败' }
    } finally { Pop-Location }
}
Write-Output 'TangtangHarness 独立环境就绪；启动默认观察模式。'
