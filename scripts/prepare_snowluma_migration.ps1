Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
& $python (Join-Path $PSScriptRoot 'validate_qq_config.py') '--env' (Join-Path $root '.env')
if ($LASTEXITCODE -ne 0) { throw 'QQ configuration validation failed.' }
& (Join-Path $PSScriptRoot 'backup.ps1')
if ($LASTEXITCODE -ne 0) { throw 'SQLite backup failed; SnowLuma preparation was stopped.' }
& (Join-Path $PSScriptRoot 'install_snowluma.ps1')
& (Join-Path $PSScriptRoot 'configure_snowluma.ps1') -EnableOneBot:$false
& (Join-Path $PSScriptRoot 'start_snowluma.ps1') -EnableOneBot:$false
Write-Output 'SnowLuma preparation is complete. NapCat remains the active OneBot transport.'
Write-Output 'Open the SnowLuma WebUI, review and accept its agreements, and change the initial password.'
Write-Output 'Do not attach the production QQ process until begin_snowluma_cutover.ps1 stops NapCat.'
