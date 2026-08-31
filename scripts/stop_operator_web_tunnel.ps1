param([int]$ProxyPort = 18767)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -like '*cloudflared*' -and $_.CommandLine -like "*$ProxyPort*") -or
    ($_.Name -like 'python*.exe' -and $_.CommandLine -like '*operator_web_proxy.py*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Output "Stopped PID $($_.ProcessId)" }
Remove-Item -LiteralPath (Join-Path $Root 'data\operator_web_tunnel_url.txt') -Force -ErrorAction SilentlyContinue
