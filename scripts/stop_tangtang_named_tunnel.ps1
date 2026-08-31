param(
    [int]$GatewayPort = 18769,
    [switch]$PreserveGuardState
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$targets = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -like '*cloudflared*' -and $_.CommandLine -like '*tangtang-web.yml*') -or
    ($_.Name -like 'python*.exe' -and $_.CommandLine -like '*tangtang_web_gateway.py*')
})
foreach ($process in $targets) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Output "Stopped Tangtang web process PID $($process.ProcessId)"
}
if ($targets.Count -eq 0) { Write-Output 'No Tangtang named tunnel process was running.' }

foreach ($relativePath in @(
    'data\global_announcement_tunnel_url.txt',
    'data\operator_web_tunnel_url.txt'
)) {
    Remove-Item -LiteralPath (Join-Path $Root $relativePath) -Force -ErrorAction SilentlyContinue
}
if (-not $PreserveGuardState) {
    New-Item -ItemType File -Force -Path (Join-Path $Root 'data\nte_tunnel_disabled.flag') | Out-Null
    Write-Output 'Web tunnel guard is disabled until a web tunnel is started manually.'
}
