Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$transport = Get-QqTransportSettings -Root $root

Write-Host '[0/8] Stopping the QQ transport watchdog...'
& (Join-Path $PSScriptRoot 'stop_watchdog.ps1')

Write-Host '[1/8] Stopping the fixed Tangtang web tunnel without changing its enabled state...'
& (Join-Path $PSScriptRoot 'stop_tangtang_named_tunnel.ps1') -PreserveGuardState

Write-Host '[2/8] Stopping the NTE login tunnel without changing its enabled state...'
& (Join-Path $PSScriptRoot 'stop_nte_tunnel.ps1') -PreserveGuardState

Write-Host '[3/8] Stopping the announcement web tunnel...'
& (Join-Path $PSScriptRoot 'stop_global_announcement_tunnel.ps1')

Write-Host '[4/8] Stopping the operator web tunnel...'
& (Join-Path $PSScriptRoot 'stop_operator_web_tunnel.ps1')

Write-Host "[5/8] Stopping verified QQ transport: $($transport.Transport)..."
& (Join-Path $PSScriptRoot 'stop_qq_transport.ps1')
if ($transport.Transport -ne 'snowluma') {
    Write-Host '[5/8] Stopping the verified SnowLuma onboarding process, if present...'
    & (Join-Path $PSScriptRoot 'stop_snowluma.ps1')
}

Write-Host '[6/8] Stopping the bot...'
& (Join-Path $PSScriptRoot 'stop.ps1')

Write-Host '[7/8] Stopping GsUID Core...'
& (Join-Path $PSScriptRoot 'stop_gsuid_core.ps1')

Write-Host '[8/8] Verifying process state...'
$pythonPath = [regex]::Escape((Join-Path $root '.venv\Scripts\python.exe'))
$botResidual = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $pythonPath -and $_.CommandLine -match '(?i)(-m\s+bot|bot\.__main__)'
})
if ($botResidual.Count -eq 0) {
    Write-Host 'Bot process check: clear.'
} else {
    Write-Warning "Bot process check found remaining PID(s): $($botResidual.ProcessId -join ', ')."
}
$transportResidual = @(Get-ConfiguredTransportProcesses -Settings $transport)
if ($transportResidual.Count -eq 0) {
    Write-Host 'QQ transport process check: clear.'
} else {
    $remainingPids = @($transportResidual | ForEach-Object { Get-QqTransportProcessId -Process $_ })
    Write-Warning "QQ transport process check found remaining PID(s): $($remainingPids -join ', ')."
}
