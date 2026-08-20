param(
    [int]$ProxyPort = 18765,
    [switch]$PreserveGuardState
)

$ErrorActionPreference = "Stop"

$tunnel = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "*cloudflared*" -and $_.CommandLine -like "*$ProxyPort*"
})
foreach ($process in $tunnel) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Output "Stopped cloudflared PID $($process.ProcessId)"
}

$proxy = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*.exe" -and $_.CommandLine -like "*nte_login_proxy.py*"
})
foreach ($process in $proxy) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Output "Stopped login proxy PID $($process.ProcessId)"
}

if ($tunnel.Count -eq 0 -and $proxy.Count -eq 0) {
    Write-Output "No NTE tunnel or login proxy process was running."
}

if (-not $PreserveGuardState) {
    $Root = Split-Path -Parent $PSScriptRoot
    $disabledFlag = Join-Path $Root "data\nte_tunnel_disabled.flag"
    New-Item -ItemType File -Force -Path $disabledFlag | Out-Null
    Write-Output "Tunnel guard is now disabled; run 启动工具\21-启动异环登录隧道.bat to re-enable it."
}
