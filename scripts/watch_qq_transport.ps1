param(
    [switch]$Once,
    [ValidateRange(10, 300)][int]$CheckIntervalSeconds = 30,
    [ValidateRange(2, 10)][int]$MissingChecksBeforeRecovery = 3,
    [ValidateRange(300, 86400)][int]$TransportRecoveryCooldownSeconds = 900,
    [ValidateRange(60, 3600)][int]$BotRecoveryCooldownSeconds = 300,
    [ValidateRange(300, 86400)][int]$NteTunnelRecoveryCooldownSeconds = 900,
    [ValidateRange(60, 3600)][int]$ModelGatewayRecoveryCooldownSeconds = 120,
    [ValidateRange(30, 300)][int]$RecoveryProcessTimeoutSeconds = 120,
    [switch]$LibraryOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')

$LogDir = Join-Path $Root 'logs'
$LogPath = Join-Path $LogDir 'qq-transport-watchdog.log'
$PidPath = Join-Path $LogDir 'qq-transport-watchdog.pid'
$StatePath = Join-Path $Root 'data\qq-transport-watchdog-state.json'
$WatchScriptPath = [IO.Path]::GetFullPath($PSCommandPath)

function Write-WatchdogLog {
    param(
        [Parameter(Mandatory)][string]$Event,
        [string]$Detail = ''
    )

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $suffix = if ($Detail) { " $Detail" } else { '' }
    $line = '{0} {1}{2}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), $Event, $suffix
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    Write-Host $line
}

function New-WatchdogState {
    return [pscustomobject]@{
        missing_connection_checks = 0
        missing_listener_checks = 0
        missing_model_gateway_checks = 0
        last_model_gateway_recovery_at = ''
        last_model_gateway_status = ''
        last_transport_recovery_at = ''
        last_bot_recovery_at = ''
        last_nte_tunnel_recovery_at = ''
        last_status = ''
        last_nte_tunnel_status = ''
        last_check_started_at = ''
        last_check_completed_at = ''
    }
}

function Get-WatchdogState {
    if (-not (Test-Path -LiteralPath $StatePath)) { return New-WatchdogState }
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding utf8 | ConvertFrom-Json
        foreach ($name in @(
            'missing_connection_checks', 'missing_listener_checks',
            'missing_model_gateway_checks', 'last_model_gateway_recovery_at', 'last_model_gateway_status',
            'last_transport_recovery_at', 'last_bot_recovery_at',
            'last_nte_tunnel_recovery_at', 'last_status', 'last_nte_tunnel_status',
            'last_check_started_at', 'last_check_completed_at'
        )) {
            if ($state.PSObject.Properties.Name -notcontains $name) {
                $state | Add-Member -NotePropertyName $name -NotePropertyValue $(if ($name -like 'missing_*') { 0 } else { '' })
            }
        }
        return $state
    } catch {
        Write-WatchdogLog -Event 'state_reset' -Detail 'reason=invalid_state_file'
        return New-WatchdogState
    }
}

function Save-WatchdogState {
    param([Parameter(Mandatory)][object]$State)

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $StatePath) | Out-Null
    $State | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Set-WatchdogStatus {
    param(
        [Parameter(Mandatory)][object]$State,
        [Parameter(Mandatory)][string]$Status,
        [string]$Detail = ''
    )

    if ([string]$State.last_status -ne $Status) {
        Write-WatchdogLog -Event "status=$Status" -Detail $Detail
        $State.last_status = $Status
    }
}

function Test-RecoveryCooldown {
    param(
        [string]$Timestamp,
        [Parameter(Mandatory)][int]$CooldownSeconds
    )

    if (-not $Timestamp) { return $true }
    try {
        return ((Get-Date).ToUniversalTime() - [DateTime]::Parse($Timestamp).ToUniversalTime()).TotalSeconds -ge $CooldownSeconds
    } catch {
        return $true
    }
}

function Invoke-BoundedPowerShellScript {
    param(
        [Parameter(Mandatory)][string]$ScriptPath,
        [ValidateRange(1, 600)][int]$TimeoutSeconds = 120,
        [string[]]$ScriptArguments = @()
    )

    $engineArgs = @(
        '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $ScriptPath)
    ) + $ScriptArguments
    $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $engineArgs -WindowStyle Hidden -PassThru
    try {
        Wait-Process -Id $process.Id -Timeout $TimeoutSeconds -ErrorAction Stop
    } catch {
        $stillRunning = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
        if ($null -ne $stillRunning) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            Wait-Process -Id $process.Id -Timeout 10 -ErrorAction SilentlyContinue
            return [pscustomobject]@{ Outcome = 'timed_out'; ExitCode = $null; ProcessId = $process.Id }
        }
        throw
    }
    $process.Refresh()
    return [pscustomobject]@{
        Outcome = $(if ($process.ExitCode -eq 0) { 'completed' } else { 'failed' })
        ExitCode = $process.ExitCode
        ProcessId = $process.Id
    }
}

function Get-BotProcesses {
    $python = [IO.Path]::GetFullPath((Join-Path $Root '.venv\Scripts\python.exe'))
    $pythonPattern = [regex]::Escape($python)
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match $pythonPattern -and
        $_.CommandLine -match '(?i)(-m\s+bot|bot\.__main__)'
    })
}

function Test-BotListener {
    param([Parameter(Mandatory)][int]$Port)

    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Where-Object {
            $_.LocalAddress -in @('127.0.0.1', '::1', '0.0.0.0', '::')
        }).Count -gt 0
    } catch {
        return $false
    }
}

function Test-WatchdogProcess {
    param([Parameter(Mandatory)][int]$ProcessId)

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    return $null -ne $process -and
        $process.Name -match '(?i)^(powershell|pwsh)\.exe$' -and
        [string]$process.CommandLine -match [regex]::Escape($WatchScriptPath)
}

function Invoke-WatchdogCheck {
    param(
        [Parameter(Mandatory)][object]$Settings,
        [Parameter(Mandatory)][object]$State,
        [switch]$AllowRecovery
    )

    $botProcesses = @(Get-BotProcesses)
    $listenerReady = Test-BotListener -Port $Settings.Port
    $transportProcesses = @(Get-ConfiguredTransportProcesses -Settings $Settings)
    $connections = @(
        if ($listenerReady) { Get-OneBotClientConnections -Port $Settings.Port }
    )
    $clientMatchesTransport = Test-OneBotConnectionOwnership -Settings $Settings -TransportProcesses $transportProcesses -Connections $connections

    if (-not $listenerReady) {
        $State.missing_listener_checks = [int]$State.missing_listener_checks + 1
        $State.missing_connection_checks = 0
        Set-WatchdogStatus -State $State -Status 'bot_unavailable' -Detail "bot_processes=$($botProcesses.Count)"
        if (
            $AllowRecovery -and
            $State.missing_listener_checks -ge $MissingChecksBeforeRecovery -and
            (Test-RecoveryCooldown -Timestamp ([string]$State.last_bot_recovery_at) -CooldownSeconds $BotRecoveryCooldownSeconds)
        ) {
            if ($botProcesses.Count -gt 0) { & (Join-Path $PSScriptRoot 'stop.ps1') }
            & (Join-Path $PSScriptRoot 'start.ps1')
            $State.last_bot_recovery_at = [DateTime]::UtcNow.ToString('o')
            $State.missing_listener_checks = 0
            Write-WatchdogLog -Event 'bot_recovery_started'
        }
        return [pscustomobject]@{
            Status = 'bot_unavailable'
            Transport = $Settings.Transport
            BotProcessCount = $botProcesses.Count
            BotListening = $false
            OneBotClientPids = @()
            TransportProcessPids = @($transportProcesses | ForEach-Object { Get-QqTransportProcessId -Process $_ })
        }
    }

    $State.missing_listener_checks = 0
    if ($connections.Count -eq 1 -and $clientMatchesTransport) {
        $State.missing_connection_checks = 0
        Set-WatchdogStatus -State $State -Status 'healthy' -Detail "transport=$($Settings.Transport) client_pid=$($connections[0].OwningProcess)"
        return [pscustomobject]@{
            Status = 'healthy'
            Transport = $Settings.Transport
            BotProcessCount = $botProcesses.Count
            BotListening = $true
            OneBotClientPids = @([int]$connections[0].OwningProcess)
            TransportProcessPids = @($transportProcesses | ForEach-Object { Get-QqTransportProcessId -Process $_ })
        }
    }
    if ($connections.Count -gt 1) {
        $State.missing_connection_checks = 0
        Set-WatchdogStatus -State $State -Status 'multiple_onebot_clients' -Detail "count=$($connections.Count)"
        return [pscustomobject]@{
            Status = 'multiple_onebot_clients'
            Transport = $Settings.Transport
            BotProcessCount = $botProcesses.Count
            BotListening = $true
            OneBotClientPids = @($connections | ForEach-Object { [int]$_.OwningProcess })
            TransportProcessPids = @($transportProcesses | ForEach-Object { Get-QqTransportProcessId -Process $_ })
        }
    }
    if ($connections.Count -eq 1) {
        $State.missing_connection_checks = 0
        Set-WatchdogStatus -State $State -Status 'unexpected_onebot_client' -Detail "transport=$($Settings.Transport) client_pid=$($connections[0].OwningProcess)"
        return [pscustomobject]@{
            Status = 'unexpected_onebot_client'
            Transport = $Settings.Transport
            BotProcessCount = $botProcesses.Count
            BotListening = $true
            OneBotClientPids = @([int]$connections[0].OwningProcess)
            TransportProcessPids = @($transportProcesses | ForEach-Object { Get-QqTransportProcessId -Process $_ })
        }
    }

    $State.missing_connection_checks = [int]$State.missing_connection_checks + 1
    $status = if ($transportProcesses.Count -gt 0) { 'transport_waiting_for_manual_login' } else { 'transport_not_connected' }
    Set-WatchdogStatus -State $State -Status $status -Detail "transport=$($Settings.Transport) misses=$($State.missing_connection_checks)"

    if (
        $AllowRecovery -and
        $State.missing_connection_checks -ge $MissingChecksBeforeRecovery -and
        (Test-RecoveryCooldown -Timestamp ([string]$State.last_transport_recovery_at) -CooldownSeconds $TransportRecoveryCooldownSeconds)
    ) {
        if ($transportProcesses.Count -gt 0) {
            Write-WatchdogLog -Event 'transport_recovery_deferred' -Detail "reason=running transport=$($Settings.Transport)"
        } else {
            $recovery = Invoke-BoundedPowerShellScript -ScriptPath (Join-Path $PSScriptRoot 'start_qq_transport.ps1') -TimeoutSeconds $RecoveryProcessTimeoutSeconds
            Write-WatchdogLog -Event 'transport_launcher_finished' -Detail "transport=$($Settings.Transport) outcome=$($recovery.Outcome)"
        }
        $State.last_transport_recovery_at = [DateTime]::UtcNow.ToString('o')
        $State.missing_connection_checks = 0
    }

    return [pscustomobject]@{
        Status = $status
        Transport = $Settings.Transport
        BotProcessCount = $botProcesses.Count
        BotListening = $true
        OneBotClientPids = @()
        TransportProcessPids = @($transportProcesses | ForEach-Object { Get-QqTransportProcessId -Process $_ })
    }
}

function Test-NteTunnelHealthy {
    $namedConfig = Join-Path $Root 'data\cloudflared\tangtang-web.yml'
    if (Test-Path -LiteralPath $namedConfig) {
        $cloudflared = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -like '*cloudflared*' -and $_.CommandLine -like '*tangtang-web.yml*'
        })
        $proxy = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -like 'python*.exe' -and $_.CommandLine -like '*tangtang_web_gateway.py*'
        })
        $listener = @(Get-NetTCPConnection -State Listen -LocalPort 18769 -ErrorAction SilentlyContinue)
    } else {
        $cloudflared = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -like '*cloudflared*' -and $_.CommandLine -like '*tunnel*--url*'
        })
        $proxy = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -like 'python*.exe' -and $_.CommandLine -like '*nte_login_proxy.py*'
        })
        $listener = @(Get-NetTCPConnection -State Listen -LocalPort 18765 -ErrorAction SilentlyContinue)
    }
    $edgeConnections = @(
        foreach ($process in $cloudflared) {
            Get-NetTCPConnection -OwningProcess $process.ProcessId -State Established -ErrorAction SilentlyContinue |
                Where-Object { $_.RemoteAddress -notin @('127.0.0.1', '::1', '0.0.0.0', '::') }
        }
    )
    return $cloudflared.Count -gt 0 -and $proxy.Count -gt 0 -and $listener.Count -gt 0 -and $edgeConnections.Count -gt 0
}

function Invoke-NteTunnelCheck {
    param(
        [Parameter(Mandatory)][object]$State,
        [switch]$AllowRecovery
    )

    $cloudflaredPath = Join-Path $Root 'tools\cloudflared.exe'
    $disabledFlag = Join-Path $Root 'data\nte_tunnel_disabled.flag'
    if (-not (Test-Path -LiteralPath $cloudflaredPath)) {
        return [pscustomobject]@{ Status = 'nte_tunnel_not_installed'; Healthy = $false }
    }
    if (Test-Path -LiteralPath $disabledFlag) {
        return [pscustomobject]@{ Status = 'nte_tunnel_disabled'; Healthy = $false }
    }
    if (Test-NteTunnelHealthy) {
        return [pscustomobject]@{ Status = 'nte_tunnel_healthy'; Healthy = $true }
    }
    if ($AllowRecovery -and (Test-RecoveryCooldown -Timestamp ([string]$State.last_nte_tunnel_recovery_at) -CooldownSeconds $NteTunnelRecoveryCooldownSeconds)) {
        Write-WatchdogLog -Event 'nte_tunnel_recovery_started'
        $namedConfig = Join-Path $Root 'data\cloudflared\tangtang-web.yml'
        $startScript = if (Test-Path -LiteralPath $namedConfig) {
            Join-Path $PSScriptRoot 'start_tangtang_named_tunnel.ps1'
        } else {
            Join-Path $PSScriptRoot 'start_nte_tunnel.ps1'
        }
        $scriptArguments = if (Test-Path -LiteralPath $namedConfig) { @('-SkipCoreRestart') } else { @() }
        $recovery = Invoke-BoundedPowerShellScript -ScriptPath $startScript -TimeoutSeconds $RecoveryProcessTimeoutSeconds -ScriptArguments $scriptArguments
        $State.last_nte_tunnel_recovery_at = [DateTime]::UtcNow.ToString('o')
        if ($recovery.Outcome -eq 'timed_out') {
            Write-WatchdogLog -Event 'nte_tunnel_recovery_timed_out' -Detail "timeout_seconds=$RecoveryProcessTimeoutSeconds"
        } elseif ($recovery.Outcome -eq 'failed') {
            Write-WatchdogLog -Event 'nte_tunnel_recovery_process_failed' -Detail "exit_code=$($recovery.ExitCode)"
        }
        if (Test-NteTunnelHealthy) {
            Write-WatchdogLog -Event 'nte_tunnel_recovery_succeeded'
            return [pscustomobject]@{ Status = 'nte_tunnel_recovered'; Healthy = $true }
        }
        Write-WatchdogLog -Event 'nte_tunnel_recovery_failed' -Detail "outcome=$($recovery.Outcome)"
        return [pscustomobject]@{ Status = 'nte_tunnel_recovery_failed'; Healthy = $false }
    }
    return [pscustomobject]@{ Status = 'nte_tunnel_unhealthy'; Healthy = $false }
}

function Invoke-ModelGatewayCommand {
    param([switch]$Recover)

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = Join-Path $Root '.venv\Scripts\python.exe'
    $controller = Join-Path $PSScriptRoot 'model_gateway.py'
    $startInfo.Arguments = '-X utf8 "{0}"' -f $controller
    if ($Recover) { $startInfo.Arguments += ' --recover' }
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $timeout = if ($Recover) { 40000 } else { 15000 }
        if (-not $process.WaitForExit($timeout)) {
            $process.Kill()
            return [pscustomobject]@{ status = 'timed_out'; healthy = $false }
        }
        if ($process.ExitCode -ne 0) { throw 'Model gateway controller failed.' }
        return ($stdout.GetAwaiter().GetResult() | ConvertFrom-Json)
    } finally {
        $process.Dispose()
    }
}

function Invoke-ModelGatewayCheck {
    param([Parameter(Mandatory)][object]$State, [switch]$AllowRecovery)

    $result = Invoke-ModelGatewayCommand
    if ($result.status -in @('missing', 'unresponsive')) {
        $State.missing_model_gateway_checks = [int]$State.missing_model_gateway_checks + 1
        if ($AllowRecovery -and $State.missing_model_gateway_checks -ge $MissingChecksBeforeRecovery -and
            (Test-RecoveryCooldown -Timestamp $State.last_model_gateway_recovery_at -CooldownSeconds $ModelGatewayRecoveryCooldownSeconds)) {
            $State.last_model_gateway_recovery_at = [DateTime]::UtcNow.ToString('o')
            $State.missing_model_gateway_checks = 0
            Save-WatchdogState -State $State
            Write-WatchdogLog -Event 'model_gateway_recovery_started'
            $result = Invoke-ModelGatewayCommand -Recover
            Write-WatchdogLog -Event 'model_gateway_recovery_finished' -Detail "status=$($result.status)"
        }
    } else {
        $State.missing_model_gateway_checks = 0
    }
    if ($State.last_model_gateway_status -ne $result.status) {
        Write-WatchdogLog -Event 'model_gateway_status' -Detail "status=$($result.status)"
        $State.last_model_gateway_status = [string]$result.status
    }
    return $result
}

function Invoke-WatchdogChecks {
    param([Parameter(Mandatory)][object]$Settings, [Parameter(Mandatory)][object]$State, [switch]$AllowRecovery)

    # Keep failures in one component from suppressing the other components.
    $results = @{}
    foreach ($component in @('Bot', 'NteTunnel', 'ModelGateway')) {
        try {
            $results[$component] = switch ($component) {
                'Bot' { Invoke-WatchdogCheck -Settings $Settings -State $State -AllowRecovery:$AllowRecovery }
                'NteTunnel' { Invoke-NteTunnelCheck -State $State -AllowRecovery:$AllowRecovery }
                'ModelGateway' { Invoke-ModelGatewayCheck -State $State -AllowRecovery:$AllowRecovery }
            }
        } catch {
            Write-WatchdogLog -Event 'component_check_failed' -Detail "component=$component error=$($_.Exception.GetType().Name)"
            $results[$component] = [pscustomobject]@{ Status = 'check_failed'; Healthy = $false }
            if ($component -eq 'Bot') { $State.last_status = 'check_failed' }
            if ($component -eq 'ModelGateway') { $State.last_model_gateway_status = 'check_failed' }
        }
    }
    if ($State.last_nte_tunnel_status -ne [string]$results.NteTunnel.Status) {
        Write-WatchdogLog -Event ('nte_tunnel_status=' + $results.NteTunnel.Status)
    }
    $State.last_nte_tunnel_status = [string]$results.NteTunnel.Status
    return [pscustomobject]$results
}

if ($LibraryOnly) { return }

$settings = Get-QqTransportSettings -Root $Root
if ($Once) {
    $state = Get-WatchdogState
    $result = Invoke-WatchdogChecks -Settings $settings -State $state
    $result | ConvertTo-Json -Depth 5 -Compress
    exit 0
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (Test-Path -LiteralPath $PidPath) {
    $existingPid = Get-Content -LiteralPath $PidPath -Raw -ErrorAction SilentlyContinue
    if ($existingPid -match '^\d+$' -and [int]$existingPid -ne $PID -and (Test-WatchdogProcess -ProcessId ([int]$existingPid))) {
        Write-Host "QQ transport watchdog is already running with PID $existingPid."
        exit 0
    }
}

$PID | Set-Content -LiteralPath $PidPath -Encoding ascii
Write-WatchdogLog -Event 'watchdog_started' -Detail "transport=$($settings.Transport) interval_seconds=$CheckIntervalSeconds"
try {
    while ($true) {
        $state = Get-WatchdogState
        $state.last_check_started_at = [DateTime]::UtcNow.ToString('o')
        Save-WatchdogState -State $state
        try {
            [void](Invoke-WatchdogChecks -Settings $settings -State $state -AllowRecovery)
        } catch {
            Write-WatchdogLog -Event 'watchdog_check_failed' -Detail "error=$($_.Exception.GetType().Name)"
        } finally {
            $state.last_check_completed_at = [DateTime]::UtcNow.ToString('o')
            Save-WatchdogState -State $state
        }
        Start-Sleep -Seconds $CheckIntervalSeconds
    }
} finally {
    if ((Test-Path -LiteralPath $PidPath) -and (Get-Content -LiteralPath $PidPath -Raw).Trim() -eq [string]$PID) {
        Remove-Item -LiteralPath $PidPath -Force
    }
    Write-WatchdogLog -Event 'watchdog_stopped'
}
