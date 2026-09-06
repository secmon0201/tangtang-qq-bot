param([switch]$Json)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
$processes = @(Get-ConfiguredTransportProcesses -Settings $settings)
$connections = @(Get-OneBotClientConnections -Port $settings.Port)
$listener = @(Get-NetTCPConnection -State Listen -LocalPort $settings.Port -ErrorAction SilentlyContinue).Count -gt 0
$clientMatchesTransport = Test-OneBotConnectionOwnership -Settings $settings -TransportProcesses $processes -Connections $connections
$snowLumaInstalled = Test-Path -LiteralPath (Join-Path $settings.SnowLumaDir 'index.mjs')
$webUi = if ($snowLumaInstalled) {
    @(Get-NetTCPConnection -State Listen -LocalPort $settings.SnowLumaWebUiPort -ErrorAction SilentlyContinue).Count -gt 0
} else { $null }
$status = [pscustomobject]@{
    transport = $settings.Transport
    transport_process_count = $processes.Count
    transport_process_ids = @($processes | ForEach-Object { Get-QqTransportProcessId -Process $_ })
    nonebot_listener = $listener
    onebot_connection_count = $connections.Count
    onebot_client_process_ids = @($connections | ForEach-Object { [int]$_.OwningProcess } | Sort-Object -Unique)
    onebot_client_matches_transport = $clientMatchesTransport
    snowluma_webui = $webUi
    healthy = $listener -and $clientMatchesTransport
}
if ($Json) {
    $status | ConvertTo-Json -Compress
} else {
    Write-Output "QQ transport: $($status.transport)"
    Write-Output "Transport processes: $($status.transport_process_count)"
    Write-Output "NoneBot listener: $($status.nonebot_listener)"
    Write-Output "OneBot clients: $($status.onebot_connection_count)"
    Write-Output "OneBot client matches transport: $($status.onebot_client_matches_transport)"
    if ($null -ne $status.snowluma_webui) { Write-Output "SnowLuma WebUI: $($status.snowluma_webui)" }
    Write-Output "Healthy: $($status.healthy)"
}
