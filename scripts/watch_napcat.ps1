param(
    [switch]$Once,
    [ValidateRange(10, 300)][int]$CheckIntervalSeconds = 30,
    [ValidateRange(2, 10)][int]$MissingChecksBeforeRecovery = 3,
    [ValidateRange(300, 86400)][int]$NapCatRecoveryCooldownSeconds = 900,
    [ValidateRange(60, 3600)][int]$BotRecoveryCooldownSeconds = 300,
    [ValidateRange(300, 86400)][int]$NteTunnelRecoveryCooldownSeconds = 900
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "napcat_process.ps1")

$LogDir = Join-Path $Root "logs"
$LogPath = Join-Path $LogDir "napcat-watchdog.log"
$PidPath = Join-Path $LogDir "napcat-watchdog.pid"
$StatePath = Join-Path $Root "data\napcat-watchdog-state.json"
$WatchScriptPath = [IO.Path]::GetFullPath($PSCommandPath)

function Write-WatchdogLog {
    param(
        [Parameter(Mandatory)][string]$Event,
        [string]$Detail = ""
    )

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $line = "{0} {1}{2}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Event, $(if ($Detail) { " $Detail" } else { "" })
    Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8
    Write-Host $line
}

function Get-WatchdogState {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return [pscustomobject]@{
            missing_connection_checks = 0
            missing_listener_checks = 0
            last_napcat_recovery_at = ""
            last_bot_recovery_at = ""
            last_nte_tunnel_recovery_at = ""
            last_status = ""
            last_nte_tunnel_status = ""
        }
    }
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw -Encoding utf8 | ConvertFrom-Json
        foreach ($name in @(
            "missing_connection_checks",
            "missing_listener_checks",
            "last_napcat_recovery_at",
            "last_bot_recovery_at",
            "last_nte_tunnel_recovery_at",
            "last_status",
            "last_nte_tunnel_status"
        )) {
            if ($state.PSObject.Properties.Name -notcontains $name) {
                $state | Add-Member -NotePropertyName $name -NotePropertyValue $(if ($name -like "missing_*") { 0 } else { "" })
            }
        }
        return $state
    } catch {
        Write-WatchdogLog -Event "state_reset" -Detail "reason=invalid_state_file"
        return [pscustomobject]@{
            missing_connection_checks = 0
            missing_listener_checks = 0
            last_napcat_recovery_at = ""
            last_bot_recovery_at = ""
            last_nte_tunnel_recovery_at = ""
            last_status = ""
            last_nte_tunnel_status = ""
        }
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
        [string]$Detail = ""
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

    if (-not $Timestamp) {
        return $true
    }
    try {
        return ((Get-Date).ToUniversalTime() - [DateTime]::Parse($Timestamp).ToUniversalTime()).TotalSeconds -ge $CooldownSeconds
    } catch {
        return $true
    }
}

function Get-BotProcesses {
    $python = [IO.Path]::GetFullPath((Join-Path $Root ".venv\Scripts\python.exe"))
    $pythonPattern = [regex]::Escape($python)
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match $pythonPattern -and
        $_.CommandLine -match "(?i)(-m\s+bot|bot\.__main__)"
    })
}

function Test-BotListener {
    param([Parameter(Mandatory)][int]$Port)

    try {
        return @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Where-Object {
            $_.LocalAddress -in @("127.0.0.1", "::1", "0.0.0.0", "::")
        }).Count -gt 0
    } catch {
        return $false
    }
}

function Start-NapCatLauncher {
    param([Parameter(Mandatory)][object]$Settings)

    $launcher = Get-NapCatLauncherPath -NapCatDir $Settings.NapCatDir
    $launcherCommand = '""{0}" -q {1}"' -f $launcher, $Settings.AccountId
    Start-Process -FilePath $env:ComSpec -ArgumentList @("/d", "/c", $launcherCommand) -WorkingDirectory $Settings.NapCatDir | Out-Null
}

function Test-WatchdogProcess {
    param([Parameter(Mandatory)][int]$ProcessId)

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    return $null -ne $process -and
        $process.Name -match "(?i)^(powershell|pwsh)\.exe$" -and
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
    $roots = @(Get-QqRootProcesses)
    $connectedRoots = @()
    if ($listenerReady) {
        $connectedRoots = @(Get-QqRootsConnectedToPort -Port $Settings.Port)
    }

    if (-not $listenerReady) {
        $State.missing_listener_checks = [int]$State.missing_listener_checks + 1
        $State.missing_connection_checks = 0
        Set-WatchdogStatus -State $State -Status "bot_unavailable" -Detail "bot_processes=$($botProcesses.Count)"
        if ($AllowRecovery -and $State.missing_listener_checks -ge $MissingChecksBeforeRecovery -and (Test-RecoveryCooldown -Timestamp ([string]$State.last_bot_recovery_at) -CooldownSeconds $BotRecoveryCooldownSeconds)) {
            if ($botProcesses.Count -gt 0) {
                & (Join-Path $PSScriptRoot "stop.ps1")
            }
            & (Join-Path $PSScriptRoot "start.ps1")
            $State.last_bot_recovery_at = [DateTime]::UtcNow.ToString("o")
            $State.missing_listener_checks = 0
            Write-WatchdogLog -Event "bot_recovery_started"
        }
        return [pscustomobject]@{
            Status = "bot_unavailable"
            BotProcessCount = $botProcesses.Count
            BotListening = $false
            ConnectedNapCatPids = @()
            QqRootPids = @($roots | ForEach-Object { $_.Pid })
        }
    }

    $State.missing_listener_checks = 0
    if ($connectedRoots.Count -gt 0) {
        $State.missing_connection_checks = 0
        if ($connectedRoots.Count -eq 1) {
            Save-QuickNapCatState -StatePath $Settings.StatePath -Root $connectedRoots[0] -AccountId $Settings.AccountId
        }
        Set-WatchdogStatus -State $State -Status "healthy" -Detail "napcat_pids=$($connectedRoots.Pid -join ',')"
        return [pscustomobject]@{
            Status = "healthy"
            BotProcessCount = $botProcesses.Count
            BotListening = $true
            ConnectedNapCatPids = @($connectedRoots.Pid)
            QqRootPids = @($roots.Pid)
        }
    }

    $State.missing_connection_checks = [int]$State.missing_connection_checks + 1
    $tracked = Get-VerifiedStateRoot -Roots $roots -StatePath $Settings.StatePath -AccountId $Settings.AccountId
    $accountRoot = @($roots | Where-Object { $_.AccountIds -contains $Settings.AccountId } | Select-Object -First 1)
    $knownRoot = if ($null -ne $tracked) { $tracked } elseif ($accountRoot.Count -eq 1) { $accountRoot[0] } else { $null }

    if ($null -ne $knownRoot) {
        Set-WatchdogStatus -State $State -Status "qq_waiting_for_manual_login" -Detail "qq_pid=$($knownRoot.Pid)"
    } else {
        Set-WatchdogStatus -State $State -Status "napcat_not_connected" -Detail "misses=$($State.missing_connection_checks)"
    }

    if ($AllowRecovery -and $State.missing_connection_checks -ge $MissingChecksBeforeRecovery -and (Test-RecoveryCooldown -Timestamp ([string]$State.last_napcat_recovery_at) -CooldownSeconds $NapCatRecoveryCooldownSeconds)) {
        if ($null -ne $knownRoot) {
            Write-WatchdogLog -Event "napcat_recovery_deferred" -Detail "reason=existing_qq_process pid=$($knownRoot.Pid)"
        } else {
            Start-NapCatLauncher -Settings $Settings
            Write-WatchdogLog -Event "napcat_launcher_started" -Detail "reason=no_verified_qq_process"
        }
        $State.last_napcat_recovery_at = [DateTime]::UtcNow.ToString("o")
        $State.missing_connection_checks = 0
    }

    return [pscustomobject]@{
        Status = if ($null -ne $knownRoot) { "qq_waiting_for_manual_login" } else { "napcat_not_connected" }
        BotProcessCount = $botProcesses.Count
        BotListening = $true
        ConnectedNapCatPids = @()
        QqRootPids = @($roots | ForEach-Object { $_.Pid })
    }
}

function Test-NteTunnelHealthy {
    $cloudflared = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like "*cloudflared*" -and $_.CommandLine -like "*tunnel*--url*"
    })
    $proxy = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like "python*.exe" -and $_.CommandLine -like "*nte_login_proxy.py*"
    })
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort 18765 -ErrorAction SilentlyContinue)
    return $cloudflared.Count -gt 0 -and $proxy.Count -gt 0 -and $listener.Count -gt 0
}

function Invoke-NteTunnelCheck {
    param(
        [Parameter(Mandatory)][object]$State,
        [switch]$AllowRecovery
    )

    $cloudflaredPath = Join-Path $Root "tools\cloudflared.exe"
    $disabledFlag = Join-Path $Root "data\nte_tunnel_disabled.flag"
    if (-not (Test-Path -LiteralPath $cloudflaredPath)) {
        return [pscustomobject]@{ Status = "nte_tunnel_not_installed"; Healthy = $false }
    }
    if (Test-Path -LiteralPath $disabledFlag) {
        return [pscustomobject]@{ Status = "nte_tunnel_disabled"; Healthy = $false }
    }
    if (Test-NteTunnelHealthy) {
        return [pscustomobject]@{ Status = "nte_tunnel_healthy"; Healthy = $true }
    }
    if ($AllowRecovery -and (Test-RecoveryCooldown -Timestamp ([string]$State.last_nte_tunnel_recovery_at) -CooldownSeconds $NteTunnelRecoveryCooldownSeconds)) {
        Write-WatchdogLog -Event "nte_tunnel_recovery_started"
        $startScript = Join-Path $PSScriptRoot "start_nte_tunnel.ps1"
        Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $startScript) `
            -WindowStyle Hidden -Wait | Out-Null
        $State.last_nte_tunnel_recovery_at = [DateTime]::UtcNow.ToString("o")
        if (Test-NteTunnelHealthy) {
            Write-WatchdogLog -Event "nte_tunnel_recovery_succeeded"
            return [pscustomobject]@{ Status = "nte_tunnel_recovered"; Healthy = $true }
        }
        Write-WatchdogLog -Event "nte_tunnel_recovery_failed"
        return [pscustomobject]@{ Status = "nte_tunnel_recovery_failed"; Healthy = $false }
    }
    return [pscustomobject]@{ Status = "nte_tunnel_unhealthy"; Healthy = $false }
}

$settings = Get-BotLaunchSettings -Root $Root
if ($Once) {
    $state = Get-WatchdogState
    $result = Invoke-WatchdogCheck -Settings $settings -State $state
    $tunnel = Invoke-NteTunnelCheck -State $state
    $result | Add-Member -NotePropertyName "NteTunnel" -NotePropertyValue $tunnel
    $result | ConvertTo-Json -Compress
    exit 0
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (Test-Path -LiteralPath $PidPath) {
    $existingPid = Get-Content -LiteralPath $PidPath -Raw -ErrorAction SilentlyContinue
    if ($existingPid -match "^\d+$" -and [int]$existingPid -ne $PID -and (Test-WatchdogProcess -ProcessId ([int]$existingPid))) {
        Write-Host "NapCat watchdog is already running with PID $existingPid."
        exit 0
    }
}

$PID | Set-Content -LiteralPath $PidPath -Encoding ascii
Write-WatchdogLog -Event "watchdog_started" -Detail "interval_seconds=$CheckIntervalSeconds"
try {
    while ($true) {
        $state = Get-WatchdogState
        try {
            [void](Invoke-WatchdogCheck -Settings $settings -State $state -AllowRecovery)
            $tunnel = Invoke-NteTunnelCheck -State $state -AllowRecovery
            if ([string]$State.last_nte_tunnel_status -ne [string]$tunnel.Status) {
                Write-WatchdogLog -Event ("nte_tunnel_status=" + $tunnel.Status)
                $State.last_nte_tunnel_status = [string]$tunnel.Status
            }
            Save-WatchdogState -State $state
        } catch {
            Write-WatchdogLog -Event "watchdog_check_failed" -Detail "error=$($_.Exception.GetType().Name)"
        }
        Start-Sleep -Seconds $CheckIntervalSeconds
    }
} finally {
    if ((Test-Path -LiteralPath $PidPath) -and (Get-Content -LiteralPath $PidPath -Raw).Trim() -eq [string]$PID) {
        Remove-Item -LiteralPath $PidPath -Force
    }
    Write-WatchdogLog -Event "watchdog_stopped"
}
