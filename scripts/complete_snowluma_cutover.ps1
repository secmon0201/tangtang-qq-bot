Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
if ($settings.Transport -ne 'snowluma') { throw 'QQ_PLATFORM_TRANSPORT is not snowluma.' }
$processes = @(Get-ConfiguredTransportProcesses -Settings $settings)
$connections = @(Get-OneBotClientConnections -Port $settings.Port)
if ($processes.Count -ne 1) { throw "Expected one SnowLuma process, found $($processes.Count)." }
if ($connections.Count -ne 1) { throw "Expected one OneBot client, found $($connections.Count)." }
$snowLumaPid = Get-QqTransportProcessId -Process $processes[0]
if ([int]$connections[0].OwningProcess -ne $snowLumaPid) {
    throw 'The connected OneBot client is not the tracked SnowLuma process; refusing to finalize.'
}

$python = Join-Path $root '.venv\Scripts\python.exe'
& $python (Join-Path $PSScriptRoot 'validate_qq_config.py') '--env' $settings.EnvPath
if ($LASTEXITCODE -ne 0) { throw 'QQ configuration validation failed.' }

& (Join-Path $PSScriptRoot 'stop.ps1')
& (Join-Path $PSScriptRoot 'start.ps1')
$healthy = $false
for ($attempt = 0; $attempt -lt 120 -and -not $healthy; $attempt++) {
    Start-Sleep -Seconds 1
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort $settings.Port -ErrorAction SilentlyContinue).Count -gt 0
    $liveConnections = @(Get-OneBotClientConnections -Port $settings.Port)
    $healthy = $listener -and $liveConnections.Count -eq 1 -and [int]$liveConnections[0].OwningProcess -eq $snowLumaPid
}
if (-not $healthy) { throw 'SnowLuma did not restore its OneBot connection within 120 seconds.' }

& (Join-Path $PSScriptRoot 'start_watchdog.ps1')
Write-Output 'SnowLuma cutover finalized: one tracked gateway, one OneBot client, and the NoneBot listener recovered.'
Write-Output 'Run the live feature acceptance matrix before removing the temporary NapCat rollback runtime.'
