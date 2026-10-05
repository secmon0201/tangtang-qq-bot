param([int]$Port = 8090, [string]$Root = '', [switch]$Foreground, [switch]$Internal)
$ErrorActionPreference = 'Stop'
$harnessRoot = if ($Root) { [IO.Path]::GetFullPath($Root) } else { Split-Path -Parent $PSScriptRoot }
$python = Join-Path $harnessRoot '.venv\Scripts\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
if (-not (Test-Path -LiteralPath $python)) { throw '请先运行 TangtangHarness\scripts\setup.ps1' }
if (-not $Internal) {
    & (Join-Path $PSScriptRoot 'stack.ps1') -Root $harnessRoot -Action start-harness -Port $Port -Foreground:$Foreground
    exit $LASTEXITCODE
}
$occupied = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($occupied) { throw "端口 $Port 已被占用；未停止占用进程。" }
if ($Foreground) {
    & $python -m tangtang_harness --root $harnessRoot --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Harness 退出，代码 $LASTEXITCODE。" }
    return
}
$logDir = Join-Path $harnessRoot 'logs'
$dataDir = Join-Path $harnessRoot 'data'
New-Item -ItemType Directory -Force -Path $logDir, $dataDir | Out-Null
$arguments = '-X utf8 -u -m tangtang_harness --root "{0}" --port {1}' -f $harnessRoot, $Port
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $harnessRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'stdout.log') -RedirectStandardError (Join-Path $logDir 'stderr.log')
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    try { $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 1; Write-Output "Harness 已启动：PID=$($status.pid)，模式=$($status.mode)，控制台=http://127.0.0.1:$Port"; return } catch { }
    if ($process.HasExited) { throw 'Harness 启动失败，请查看新目录 logs\stderr.log' }
}
throw 'Harness 就绪检查超时，请查看新目录日志。'
