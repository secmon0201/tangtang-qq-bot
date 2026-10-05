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
$watchdogHealthy = $false
$processes = @()
$connections = @()
$listener = $false
$webUiReady = $null
$watchdogReady = $false
$watchdogHeartbeatReady = $false
$watchdogHeartbeatAgeSeconds = $null
$watchdogStatus = 'unknown'
$watchdogPidPath = Join-Path $root 'logs\qq-transport-watchdog.pid'
$watchdogStatePath = Join-Path $root 'data\qq-transport-watchdog-state.json'

do {
    $processes = @(Get-ConfiguredTransportProcesses -Settings $settings)
    $connections = @(Get-OneBotClientConnections -Port $settings.Port)
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort $settings.Port -ErrorAction SilentlyContinue).Count -gt 0
    $clientMatchesTransport = Test-OneBotConnectionOwnership -Settings $settings -TransportProcesses $processes -Connections $connections
    $webUiReady = if ($settings.Transport -eq 'snowluma') {
        @(Get-NetTCPConnection -State Listen -LocalPort $settings.SnowLumaWebUiPort -ErrorAction SilentlyContinue).Count -gt 0
    } else { $true }
    $healthy = $listener -and $clientMatchesTransport -and $webUiReady
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
    $watchdogHeartbeatReady = $false
    $watchdogHeartbeatAgeSeconds = $null
    $watchdogStatus = 'unknown'
    if (Test-Path -LiteralPath $watchdogStatePath -PathType Leaf) {
        try {
            $watchdogState = Get-Content -LiteralPath $watchdogStatePath -Raw -Encoding utf8 | ConvertFrom-Json
            $watchdogStatus = [string]$watchdogState.last_status
            $completedValue = $watchdogState.last_check_completed_at
            $completedAt = if ($completedValue -is [datetime]) {
                [datetimeoffset]$completedValue
            } else {
                [datetimeoffset]::Parse([string]$completedValue)
            }
            $completedAt = $completedAt.ToUniversalTime()
            $watchdogHeartbeatAgeSeconds = ([datetimeoffset]::UtcNow - $completedAt).TotalSeconds
            $watchdogHeartbeatReady = $watchdogHeartbeatAgeSeconds -ge 0 -and $watchdogHeartbeatAgeSeconds -le 180
        } catch {
            $watchdogHeartbeatReady = $false
            $watchdogStatus = 'invalid_state'
        }
    }
    $watchdogHealthy = $watchdogHeartbeatReady -and $watchdogStatus -eq 'healthy'
    $ready = $healthy -and $watchdogReady -and $watchdogHealthy
    if (-not $ready -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
} while (-not $ready -and (Get-Date) -lt $deadline)

$corePort = 8765
$watchdogSupervisorReady = $false
try {
    . (Join-Path $PSScriptRoot 'watchdog_lifecycle.ps1')
    $supervisorTask = Get-ScheduledTask -TaskName (Get-WatchdogTaskName) -ErrorAction Stop
    $watchdogSupervisorReady = (Test-Path -LiteralPath $WatchdogGate) -and $supervisorTask.State -ne 'Disabled'
} catch { $watchdogSupervisorReady = $false }

if ([string]$settings.Values['GSUID_CORE_PORT'] -match '^\d+$') {
    $corePort = [int]$settings.Values['GSUID_CORE_PORT']
}
$coreListener = @(Get-NetTCPConnection -State Listen -LocalPort $corePort -ErrorAction SilentlyContinue).Count -gt 0
$botErrorPath = Join-Path $root 'logs\bot.err.log'
$botErrorEmpty = -not (Test-Path -LiteralPath $botErrorPath) -or (Get-Item -LiteralPath $botErrorPath).Length -eq 0
$speechReady = 'not configured'
$speechConfigPath = Join-Path $root 'data\tts\service.json'
if (Test-Path -LiteralPath $speechConfigPath) {
    try {
        $speechConfig = Get-Content -LiteralPath $speechConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
        $speechGateJson = & (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'scripts\speech_switch.py') status
        if ($LASTEXITCODE -ne 0) { throw 'Cannot read speech gate.' }
        $speechEnabled = $speechConfig.enabled -and ($speechGateJson | ConvertFrom-Json).enabled
        $speechReady = if (-not $speechEnabled) { 'disabled' }
            elseif (@(Get-NetTCPConnection -State Listen -LocalPort ([int]$speechConfig.port) -ErrorAction SilentlyContinue).Count -gt 0) { 'listening; see #persona status for synthesis readiness' }
            else { 'preparing or unavailable; text fallback active' }
    } catch { $speechReady = 'configuration fault; text fallback active' }
}
$status = [pscustomobject]@{
    Transport = $settings.Transport
    TransportProcessCount = $processes.Count
    NoneBotListener = $listener
    OneBotClientCount = $connections.Count
    OneBotClientMatchesTransport = $clientMatchesTransport
    SnowLumaWebUi = $webUiReady
    Watchdog = $watchdogReady
    WatchdogHeartbeat = $watchdogHeartbeatReady
    WatchdogHeartbeatAgeSeconds = if ($null -eq $watchdogHeartbeatAgeSeconds) { $null } else { [Math]::Round($watchdogHeartbeatAgeSeconds, 1) }
    WatchdogStatus = $watchdogStatus
    WatchdogHealthy = $watchdogHealthy
    WatchdogSupervisor = $watchdogSupervisorReady
    GsUIDCore = $coreListener
    BotErrorLogEmpty = $botErrorEmpty
    SpeechRuntime = $speechReady
}

if (-not $healthy -or -not $watchdogReady -or -not $watchdogHealthy -or -not $watchdogSupervisorReady -or -not $coreListener -or -not $botErrorEmpty) {
    Write-Host 'Full-stack readiness checks did not pass:' -ForegroundColor Red
    if (-not $listener) { Write-Host ' - NoneBot is not listening on the configured port.' -ForegroundColor Red }
    if ($processes.Count -ne 1) { Write-Host " - Expected one configured transport process; found $($processes.Count)." -ForegroundColor Red }
    if ($connections.Count -eq 0) {
        Write-Host ' - No OneBot reverse WebSocket client is connected.' -ForegroundColor Red
        if ($settings.Transport -eq 'snowluma' -and $webUiReady) {
            $snowLumaProcessesUrl = "http://127.0.0.1:$($settings.SnowLumaWebUiPort)/processes"
            Write-Host " - Open $snowLumaProcessesUrl, probe the QQ processes, load the configured bot account, and complete any manual QQ verification." -ForegroundColor Yellow
        }
    } elseif ($connections.Count -gt 1) {
        Write-Host " - Expected one OneBot client; found $($connections.Count)." -ForegroundColor Red
    } elseif (-not $clientMatchesTransport) {
        Write-Host ' - The OneBot client is not owned by the configured transport process.' -ForegroundColor Red
    }
    if (-not $webUiReady) { Write-Host ' - SnowLuma WebUI is not listening.' -ForegroundColor Red }
    if (-not $watchdogReady) { Write-Host ' - The owned watchdog process is not running.' -ForegroundColor Red }
    if (-not $watchdogHeartbeatReady) { Write-Host ' - The watchdog completed heartbeat is missing or older than 180 seconds.' -ForegroundColor Red }
    elseif ($watchdogStatus -ne 'healthy') { Write-Host " - The watchdog is updating, but its current status is '$watchdogStatus'." -ForegroundColor Red }
    if (-not $watchdogSupervisorReady) { Write-Host ' - The watchdog scheduled supervisor is unavailable or disabled.' -ForegroundColor Red }
    if (-not $coreListener) { Write-Host " - GsUID Core is not listening on port $corePort." -ForegroundColor Red }
    if (-not $botErrorEmpty) { Write-Host ' - logs\bot.err.log is not empty.' -ForegroundColor Red }
    throw ('Full-stack verification failed: ' + ($status | ConvertTo-Json -Compress))
}

$status | Format-List | Out-String | Write-Host
Write-Output 'Full stack is healthy and ready.'
