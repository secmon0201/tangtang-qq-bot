param(
    [bool]$EnableOneBot = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
& (Join-Path $PSScriptRoot 'configure_snowluma.ps1') -EnableOneBot:$EnableOneBot

$existing = @(Get-ConfiguredTransportProcesses -Settings ([pscustomobject]@{
    Root = $settings.Root; Transport = 'snowluma'; AccountId = $settings.AccountId
    Port = $settings.Port; SnowLumaDir = $settings.SnowLumaDir
    LagrangeDir = $settings.LagrangeDir; NapCatDir = $settings.NapCatDir
    StatePath = Join-Path $settings.Root 'data\qq-transport-snowluma-state.json'
}))
if ($existing.Count -gt 0) {
    Write-Output "SnowLuma is already running with PID $(Get-QqTransportProcessId -Process $existing[0])."
    exit 0
}

$node = Join-Path $settings.SnowLumaDir 'node.exe'
$entry = Join-Path $settings.SnowLumaDir 'index.mjs'
if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $entry)) {
    throw "SnowLuma runtime is incomplete under: $($settings.SnowLumaDir)"
}
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir 'snowluma.out.log'
$stderr = Join-Path $logDir 'snowluma.err.log'
$process = Start-Process -FilePath $node -ArgumentList @('index.mjs') -WorkingDirectory $settings.SnowLumaDir -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 1
if ($process.HasExited) { throw "SnowLuma exited during startup with code $($process.ExitCode). Review logs\snowluma.err.log." }
$record = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)" -ErrorAction Stop
$snowSettings = [pscustomobject]@{
    Root = $settings.Root; Transport = 'snowluma'; AccountId = $settings.AccountId
    StatePath = Join-Path $settings.Root 'data\qq-transport-snowluma-state.json'
}
Save-QqTransportProcessState -Settings $snowSettings -Process $record -Executable $node

$webReady = $false
for ($attempt = 0; $attempt -lt 20 -and -not $webReady; $attempt++) {
    Start-Sleep -Milliseconds 500
    $webReady = @(Get-NetTCPConnection -State Listen -LocalPort $settings.SnowLumaWebUiPort -ErrorAction SilentlyContinue).Count -gt 0
}
if ($webReady) {
    Write-Output "SnowLuma started (PID $($process.Id)); WebUI: http://127.0.0.1:$($settings.SnowLumaWebUiPort)"
} else {
    Write-Warning "SnowLuma is running (PID $($process.Id)), but the WebUI listener is not ready. Review logs\snowluma.out.log and logs\snowluma.err.log."
}
