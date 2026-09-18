param([Parameter(Mandatory)][string]$WatchdogPath)
$ErrorActionPreference = 'Stop'
. $WatchdogPath -LibraryOnly
function Write-WatchdogLog { param($Event, $Detail) }
function Save-WatchdogState { param($State) }
$script:checks = 0
$script:recoveries = 0
$script:healthy = $false
function Invoke-ModelGatewayCommand {
    param([switch]$Recover)
    if ($Recover) { $script:recoveries++; return [pscustomobject]@{ status='healthy'; healthy=$true } }
    $script:checks++
    return [pscustomobject]@{ status=$(if ($script:healthy) {'healthy'} else {'missing'}); healthy=$script:healthy }
}
$state = New-WatchdogState
1..2 | ForEach-Object { [void](Invoke-ModelGatewayCheck -State $state -AllowRecovery) }
if ($script:recoveries -ne 0) { throw 'Recovered before threshold' }
[void](Invoke-ModelGatewayCheck -State $state -AllowRecovery)
if ($script:recoveries -ne 1) { throw 'Did not recover after three misses' }
1..3 | ForEach-Object { [void](Invoke-ModelGatewayCheck -State $state -AllowRecovery) }
if ($script:recoveries -ne 1) { throw 'Cooldown ignored' }
$script:healthy = $true
[void](Invoke-ModelGatewayCheck -State $state -AllowRecovery)
if ($state.missing_model_gateway_checks -ne 0) { throw 'Healthy check did not reset misses' }
function Invoke-WatchdogCheck { param($Settings, $State, [switch]$AllowRecovery); throw 'Synthetic bot error' }
function Invoke-NteTunnelCheck { param($State, [switch]$AllowRecovery); throw 'Synthetic tunnel error' }
$result = Invoke-WatchdogChecks -Settings ([pscustomobject]@{}) -State $state -AllowRecovery
$isolated = $result.Bot.Status -eq 'check_failed' -and $result.NteTunnel.Status -eq 'check_failed' -and $result.ModelGateway.healthy
[pscustomobject]@{ recoveries=$script:recoveries; checks=$script:checks; isolated=$isolated } | ConvertTo-Json -Compress
