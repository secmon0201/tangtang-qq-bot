Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$transport = Get-QqTransportSettings -Root $root

Write-Host '[1/8] Starting GsUID Core...'
& (Join-Path $PSScriptRoot 'start_gsuid_core.ps1') -Background

Write-Host '[2/8] Starting the bot...'
& (Join-Path $PSScriptRoot 'start.ps1')

$namedTunnelConfig = Join-Path $root 'data\cloudflared\tangtang-web.yml'
if (Test-Path -LiteralPath $namedTunnelConfig) {
    Write-Host '[3/8] Starting the fixed Tangtang web tunnel...'
    & (Join-Path $PSScriptRoot 'start_tangtang_named_tunnel.ps1')
    Write-Host '[4/8] Announcement, operator, and NTE paths share the fixed tunnel.'
    Write-Host '[5/8] Legacy Quick Tunnels were skipped.'
} else {
    Write-Host '[3/8] Starting the announcement web tunnel...'
    & (Join-Path $PSScriptRoot 'start_global_announcement_tunnel.ps1')
    Write-Host '[4/8] Starting the operator web tunnel...'
    & (Join-Path $PSScriptRoot 'start_operator_web_tunnel.ps1')
    Write-Host '[5/8] No additional feature tunnel is required.'
}

Write-Host "[6/8] Starting QQ transport: $($transport.Transport)..."
& (Join-Path $PSScriptRoot 'start_qq_transport.ps1')

if (Test-Path -LiteralPath $namedTunnelConfig) {
    Write-Host '[7/8] NTE login is already included in the fixed Tangtang web tunnel.'
} elseif (Test-Path -LiteralPath (Join-Path $root 'data\nte_tunnel_disabled.flag')) {
    Write-Host '[7/8] NTE login tunnel is intentionally disabled.'
} else {
    Write-Host '[7/8] Starting the NTE login tunnel...'
    & (Join-Path $PSScriptRoot 'start_nte_tunnel.ps1')
}

Write-Host '[8/8] Starting the QQ transport watchdog...'
& (Join-Path $PSScriptRoot 'start_watchdog.ps1')
