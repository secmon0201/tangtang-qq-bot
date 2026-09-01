param(
    [string]$Hostname = 'tangtang.secmon.cn',
    [int]$GatewayPort = 18769,
    [int]$GatewayMaxConcurrency = 32,
    [int]$GatewayClientTimeoutSeconds = 60,
    [int]$GatewayMaxRequestMB = 32,
    [int]$TimeoutSeconds = 60,
    [switch]$SkipCoreRestart
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Cloudflared = Join-Path $Root 'tools\cloudflared.exe'
$ConfigPath = Join-Path $Root 'data\cloudflared\tangtang-web.yml'
$LogDir = Join-Path $Root 'logs'
$TunnelLog = Join-Path $LogDir 'cloudflared-tangtang-web.log'
$GatewayOut = Join-Path $LogDir 'tangtang_web_gateway.out.log'
$GatewayErr = Join-Path $LogDir 'tangtang_web_gateway.err.log'

if ($GatewayMaxConcurrency -le 0 -or $GatewayClientTimeoutSeconds -le 0 -or $GatewayMaxRequestMB -le 0) {
    throw 'Gateway limits must be positive integers.'
}

foreach ($requiredPath in @($Python, $Cloudflared, $ConfigPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Required path not found: $requiredPath" }
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

& (Join-Path $PSScriptRoot 'stop_global_announcement_tunnel.ps1')
& (Join-Path $PSScriptRoot 'stop_operator_web_tunnel.ps1')
& (Join-Path $PSScriptRoot 'stop_nte_tunnel.ps1') -PreserveGuardState

Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -like '*cloudflared*' -and $_.CommandLine -like '*tangtang-web.yml*') -or
    ($_.Name -like 'python*.exe' -and $_.CommandLine -like '*tangtang_web_gateway.py*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$gatewayArgs = @(
    '-u',
    (Join-Path $PSScriptRoot 'tangtang_web_gateway.py'),
    '--port', $GatewayPort,
    '--max-concurrency', $GatewayMaxConcurrency,
    '--client-timeout-seconds', $GatewayClientTimeoutSeconds,
    '--max-request-mb', $GatewayMaxRequestMB
)
Start-Process -FilePath $Python -ArgumentList $gatewayArgs -WorkingDirectory $Root -RedirectStandardOutput $GatewayOut -RedirectStandardError $GatewayErr -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 1
$listener = @(Get-NetTCPConnection -State Listen -LocalPort $GatewayPort -ErrorAction SilentlyContinue)
if ($listener.Count -eq 0) { throw "Tangtang web gateway did not listen on port $GatewayPort." }

Remove-Item -LiteralPath $TunnelLog -Force -ErrorAction SilentlyContinue
$tunnelArgs = @('tunnel', '--config', $ConfigPath, '--protocol', 'http2', '--no-autoupdate', '--logfile', $TunnelLog, 'run')
Start-Process -FilePath $Cloudflared -ArgumentList $tunnelArgs -WorkingDirectory $Root -WindowStyle Hidden | Out-Null

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$registered = $false
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $TunnelLog) {
        $content = Get-Content -LiteralPath $TunnelLog -Raw -ErrorAction SilentlyContinue
        if ($content -match 'Registered tunnel connection') { $registered = $true; break }
    }
    Start-Sleep -Seconds 2
}
if (-not $registered) { throw "Named tunnel did not register within ${TimeoutSeconds}s. Check: $TunnelLog" }

$publicUrl = "https://$Hostname"
$utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($relativePath in @(
    'data\global_announcement_tunnel_url.txt',
    'data\operator_web_tunnel_url.txt'
)) {
    $path = Join-Path $Root $relativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    [IO.File]::WriteAllText($path, $publicUrl, $utf8)
}

$nteConfig = Join-Path $Root 'GsUID.Core\data\NTEUID\config.json'
if (Test-Path -LiteralPath $nteConfig) {
    & (Join-Path $PSScriptRoot 'set_nte_login_url.ps1') -PublicBaseUrl $publicUrl
    if (-not $SkipCoreRestart) {
        & (Join-Path $PSScriptRoot 'stop_gsuid_core.ps1')
        Start-Sleep -Seconds 2
        & (Join-Path $PSScriptRoot 'start_gsuid_core.ps1') -Background
    }
}
Remove-Item -LiteralPath (Join-Path $Root 'data\nte_tunnel_disabled.flag') -Force -ErrorAction SilentlyContinue
Write-Output "Tangtang named web tunnel ready: $publicUrl"
Write-Output 'Public homepage: / (static site)'
Write-Output 'Short links: s.secmon.cn/r, s.secmon.cn/h'
Write-Output 'Bot feature paths: /live/*, /ranking/<token>/*, /help/*, /notice/*, /duplicate/*, /nte/*'
