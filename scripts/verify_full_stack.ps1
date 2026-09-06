param(
    [ValidateRange(1, 300)][int]$WaitSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$healthy = $false
$processes = @()
$connections = @()
$listener = $false
$webUiReady = $null

do {
    $processes = @(Get-ConfiguredTransportProcesses -Settings $settings)
    $connections = @(Get-OneBotClientConnections -Port $settings.Port)
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort $settings.Port -ErrorAction SilentlyContinue).Count -gt 0
    $clientMatchesTransport = Test-OneBotConnectionOwnership -Settings $settings -TransportProcesses $processes -Connections $connections
    $webUiReady = if ($settings.Transport -eq 'snowluma') {
        @(Get-NetTCPConnection -State Listen -LocalPort $settings.SnowLumaWebUiPort -ErrorAction SilentlyContinue).Count -gt 0
    } else { $true }
    $healthy = $listener -and $clientMatchesTransport -and $webUiReady
    if (-not $healthy -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
} while (-not $healthy -and (Get-Date) -lt $deadline)

$watchdogPidPath = Join-Path $root 'logs\qq-transport-watchdog.pid'
$watchdogReady = $false
if (Test-Path -LiteralPath $watchdogPidPath -PathType Leaf) {
    $rawWatchdogPid = (Get-Content -LiteralPath $watchdogPidPath -Raw -ErrorAction SilentlyContinue).Trim()
    if ($rawWatchdogPid -match '^\d+$') {
        $watchdog = Get-CimInstance Win32_Process -Filter "ProcessId = $rawWatchdogPid" -ErrorAction SilentlyContinue
        $watchdogScript = [regex]::Escape([IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'watch_qq_transport.ps1')))
        $watchdogReady = $null -ne $watchdog -and
            $watchdog.Name -match '(?i)^(powershell|pwsh)\.exe$' -and
            [string]$watchdog.CommandLine -match $watchdogScript
    }
}

$watchdogStatePath = Join-Path $root 'data\qq-transport-watchdog-state.json'
$watchdogHeartbeatReady = $false
if (Test-Path -LiteralPath $watchdogStatePath -PathType Leaf) {
    try {
        $watchdogState = Get-Content -LiteralPath $watchdogStatePath -Raw -Encoding utf8 | ConvertFrom-Json
        $completedValue = $watchdogState.last_check_completed_at
        $completedAt = if ($completedValue -is [datetime]) {
            [datetimeoffset]$completedValue
        } else {
            [datetimeoffset]::Parse([string]$completedValue)
        }
        $completedAt = $completedAt.ToUniversalTime()
        $heartbeatAge = ([datetimeoffset]::UtcNow - $completedAt).TotalSeconds
        $watchdogHeartbeatReady = [string]$watchdogState.last_status -eq 'healthy' -and
            $heartbeatAge -ge 0 -and $heartbeatAge -le 180
    } catch {
        $watchdogHeartbeatReady = $false
    }
}

$corePort = 8765
if ([string]$settings.Values['GSUID_CORE_PORT'] -match '^\d+$') {
    $corePort = [int]$settings.Values['GSUID_CORE_PORT']
}
$coreListener = @(Get-NetTCPConnection -State Listen -LocalPort $corePort -ErrorAction SilentlyContinue).Count -gt 0
$botErrorPath = Join-Path $root 'logs\bot.err.log'
$botErrorEmpty = -not (Test-Path -LiteralPath $botErrorPath) -or (Get-Item -LiteralPath $botErrorPath).Length -eq 0
$status = [pscustomobject]@{
    Transport = $settings.Transport
    TransportProcessCount = $processes.Count
    NoneBotListener = $listener
    OneBotClientCount = $connections.Count
    OneBotClientMatchesTransport = $clientMatchesTransport
    SnowLumaWebUi = $webUiReady
    Watchdog = $watchdogReady
    WatchdogHeartbeat = $watchdogHeartbeatReady
    GsUIDCore = $coreListener
    BotErrorLogEmpty = $botErrorEmpty
}

if (-not $healthy -or -not $watchdogReady -or -not $watchdogHeartbeatReady -or -not $coreListener -or -not $botErrorEmpty) {
    throw ('Full-stack verification failed: ' + ($status | ConvertTo-Json -Compress))
}

$status | Format-List | Out-String | Write-Host
Write-Output 'Full stack is healthy and ready.'
