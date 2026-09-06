param([switch]$StartCutover)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $StartCutover) {
    throw 'Pass -StartCutover only after SnowLuma WebUI onboarding is complete. This stops the current QQ transport.'
}

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
if ($settings.Transport -eq 'snowluma') {
    Write-Output 'QQ_PLATFORM_TRANSPORT is already snowluma; no cutover state was changed.'
    exit 0
}
if (-not (Test-Path -LiteralPath (Join-Path $settings.SnowLumaDir 'index.mjs'))) {
    throw 'SnowLuma is not installed. Run prepare_snowluma_migration.ps1 first.'
}
$snowSettings = [pscustomobject]@{
    Root = $settings.Root; Transport = 'snowluma'; AccountId = $settings.AccountId
    StatePath = Join-Path $settings.Root 'data\qq-transport-snowluma-state.json'
}
$snowProcesses = @(Get-ConfiguredTransportProcesses -Settings $snowSettings)
if ($snowProcesses.Count -ne 1) {
    throw 'SnowLuma onboarding process is not uniquely running. Run prepare_snowluma_migration.ps1 first.'
}
$webUiReady = @(Get-NetTCPConnection -State Listen -LocalPort $settings.SnowLumaWebUiPort -ErrorAction SilentlyContinue).Count -gt 0
if (-not $webUiReady) { throw 'SnowLuma WebUI is not listening; refusing to stop the current QQ transport.' }
$webUiConfigPath = Join-Path $settings.SnowLumaDir 'config\webui.json'
$consentPath = Join-Path $settings.SnowLumaDir 'config\consent.json'
if (-not (Test-Path -LiteralPath $webUiConfigPath)) {
    throw 'SnowLuma WebUI onboarding has not created its credential state.'
}
try {
    $webUiConfig = Get-Content -LiteralPath $webUiConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
} catch {
    throw 'SnowLuma WebUI credential state is invalid.'
}
if ($webUiConfig.PSObject.Properties.Name -notcontains 'mustChangePassword' -or [bool]$webUiConfig.mustChangePassword) {
    throw 'Change the SnowLuma initial WebUI password before starting cutover.'
}
if (-not (Test-Path -LiteralPath $consentPath)) {
    throw 'Accept the SnowLuma EULA and privacy agreement before starting cutover.'
}
try {
    $consent = Get-Content -LiteralPath $consentPath -Raw -Encoding utf8 | ConvertFrom-Json
} catch {
    throw 'SnowLuma consent state is invalid.'
}
if (-not [string]$consent.version -or -not [string]$consent.acceptedAt) {
    throw 'SnowLuma consent state is incomplete.'
}

$python = Join-Path $root '.venv\Scripts\python.exe'
& $python (Join-Path $PSScriptRoot 'validate_qq_config.py') '--env' $settings.EnvPath
if ($LASTEXITCODE -ne 0) { throw 'QQ configuration validation failed.' }
& (Join-Path $PSScriptRoot 'backup.ps1')
if ($LASTEXITCODE -ne 0) { throw 'SQLite backup failed; cutover was not started.' }

$backupDir = Join-Path $root 'backups\snowluma-migration'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$envBackup = Join-Path $backupDir ('.env.before-snowluma-{0}' -f (Get-Date).ToString('yyyyMMdd-HHmmss'))
Copy-Item -LiteralPath $settings.EnvPath -Destination $envBackup -ErrorAction Stop
$previousTransport = $settings.Transport

try {
    & (Join-Path $PSScriptRoot 'stop_watchdog.ps1')
    & (Join-Path $PSScriptRoot 'stop_qq_transport.ps1')

    $maintenanceEnabled = [string]$settings.Values['QQ_TRANSPORT_MAINTENANCE_ENABLED']
    if (-not $maintenanceEnabled) { $maintenanceEnabled = [string]$settings.Values['NAPCAT_MAINTENANCE_ENABLED'] }
    if (-not $maintenanceEnabled) { $maintenanceEnabled = 'true' }
    $maintenanceInterval = [string]$settings.Values['QQ_TRANSPORT_MAINTENANCE_INTERVAL_SECONDS']
    if (-not $maintenanceInterval) { $maintenanceInterval = [string]$settings.Values['NAPCAT_MAINTENANCE_INTERVAL_SECONDS'] }
    if (-not $maintenanceInterval) { $maintenanceInterval = '30' }

    $snowLumaDirValue = [string]$settings.Values['SNOWLUMA_DIR']
    if (-not $snowLumaDirValue) { $snowLumaDirValue = 'SnowLuma' }
    Set-BotEnvValue -Path $settings.EnvPath -Name 'QQ_ACCOUNT_ID' -Value $settings.AccountId
    Set-BotEnvValue -Path $settings.EnvPath -Name 'SNOWLUMA_DIR' -Value $snowLumaDirValue
    Set-BotEnvValue -Path $settings.EnvPath -Name 'SNOWLUMA_WEBUI_PORT' -Value ([string]$settings.SnowLumaWebUiPort)
    Set-BotEnvValue -Path $settings.EnvPath -Name 'QQ_TRANSPORT_MAINTENANCE_ENABLED' -Value $maintenanceEnabled
    Set-BotEnvValue -Path $settings.EnvPath -Name 'QQ_TRANSPORT_MAINTENANCE_INTERVAL_SECONDS' -Value $maintenanceInterval
    Set-BotEnvValue -Path $settings.EnvPath -Name 'QQ_PLATFORM_TRANSPORT' -Value 'snowluma'

    & (Join-Path $PSScriptRoot 'configure_snowluma.ps1') -EnableOneBot:$true
    & (Join-Path $PSScriptRoot 'stop_snowluma.ps1')
    & (Join-Path $PSScriptRoot 'start_snowluma.ps1') -EnableOneBot:$true
    & (Join-Path $PSScriptRoot 'stop.ps1')
    & (Join-Path $PSScriptRoot 'start.ps1')
} catch {
    $cutoverError = $_
    $rollbackErrors = [System.Collections.Generic.List[string]]::new()
    Write-Warning "SnowLuma cutover failed; restoring the exact pre-cutover environment and $previousTransport runtime."

    $environmentRestored = $false
    try {
        Copy-Item -LiteralPath $envBackup -Destination $settings.EnvPath -Force -ErrorAction Stop
        $environmentRestored = $true
    } catch {
        $rollbackErrors.Add("environment restore failed: $($_.Exception.Message)")
    }

    $snowLumaStopped = $false
    try {
        & (Join-Path $PSScriptRoot 'stop_snowluma.ps1')
        $snowSettings = [pscustomobject]@{
            Root = $settings.Root; Transport = 'snowluma'; AccountId = $settings.AccountId
            StatePath = Join-Path $settings.Root 'data\qq-transport-snowluma-state.json'
            SnowLumaDir = $settings.SnowLumaDir
        }
        $snowLumaStopped = @(Get-ConfiguredTransportProcesses -Settings $snowSettings).Count -eq 0
        if (-not $snowLumaStopped) {
            $rollbackErrors.Add('SnowLuma is still running, so the previous QQ gateway was not restarted to avoid duplicate clients.')
        }
    } catch {
        $rollbackErrors.Add("SnowLuma stop failed: $($_.Exception.Message)")
    }

    try {
        & (Join-Path $PSScriptRoot 'stop.ps1')
        & (Join-Path $PSScriptRoot 'start.ps1')
    } catch {
        $rollbackErrors.Add("NoneBot restore failed: $($_.Exception.Message)")
    }

    if ($environmentRestored -and $snowLumaStopped) {
        try {
            & (Join-Path $PSScriptRoot 'start_qq_transport.ps1')
        } catch {
            $rollbackErrors.Add("previous QQ transport restore failed: $($_.Exception.Message)")
        }
    }
    if ($environmentRestored -and $snowLumaStopped) {
        try {
            & (Join-Path $PSScriptRoot 'start_watchdog.ps1')
        } catch {
            $rollbackErrors.Add("watchdog restore failed: $($_.Exception.Message)")
        }
    } else {
        $rollbackErrors.Add('watchdog remains stopped because the original environment or exclusive gateway state was not restored.')
    }
    if ($rollbackErrors.Count -gt 0) {
        Write-Warning ("Cutover rollback needs attention: " + ($rollbackErrors -join ' | '))
    } else {
        Write-Warning 'Cutover rollback restored the original environment and runtime chain.'
    }
    throw $cutoverError
}

Write-Output 'SnowLuma cutover has started. Complete QQ attachment/login/device verification in its WebUI now.'
Write-Output 'The watchdog remains stopped so it cannot interfere with the manual login window.'
Write-Output 'After OneBot connects, run complete_snowluma_cutover.ps1.'
