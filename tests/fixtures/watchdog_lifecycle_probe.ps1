param([string]$LibraryPath, [string]$ScratchRoot)
. $LibraryPath
$WatchdogRoot = $ScratchRoot
$WatchdogGate = Join-Path $ScratchRoot 'enabled.flag'
$WatchdogPidFile = Join-Path $ScratchRoot 'watchdog.pid'
$WatchdogStateFile = Join-Path $ScratchRoot 'state.json'
New-Item -ItemType Directory -Path (Join-Path $ScratchRoot 'logs') -Force | Out-Null
$script:starts = 0
$script:stops = 0
$script:owned = @()
function Get-OwnedWatchdogProcesses { return $script:owned }
function Stop-OwnedWatchdogProcesses { $script:stops++ }
function Start-DetachedWatchdog { $script:starts++; return 123 }
if ((Ensure-ManagedWatchdog) -ne 'disabled' -or $script:starts -ne 0) { throw 'Disabled gate was ignored' }
'enabled' | Set-Content $WatchdogGate
if ((Ensure-ManagedWatchdog) -notlike 'started*') { throw 'Missing process was not recovered' }
$now = [DateTime]::UtcNow
$script:owned = @([pscustomobject]@{ ProcessId=123; CreationDate=$now.AddHours(-1) })
@{last_check_completed_at=$now.ToString('o')} | ConvertTo-Json | Set-Content $WatchdogStateFile
if ((Ensure-ManagedWatchdog) -ne 'healthy' -or $script:starts -ne 1) { throw 'Healthy watchdog was restarted' }
@{last_check_completed_at=$now.AddMinutes(-4).ToString('o')} | ConvertTo-Json | Set-Content $WatchdogStateFile
if ((Ensure-ManagedWatchdog) -notlike 'started*' -or $script:starts -ne 2) { throw 'Stale heartbeat was ignored' }
if (-not (Test-WatchdogHeartbeatFresh -ProcessStartedAt $now.AddSeconds(-10) -Now $now)) { throw 'Missing startup grace' }
'{' | Set-Content $WatchdogStateFile
[IO.File]::SetLastWriteTimeUtc($WatchdogStateFile, $now)
if (-not (Test-WatchdogHeartbeatFresh -ProcessStartedAt $now.AddHours(-1) -Now $now)) { throw 'Concurrent heartbeat write caused a false restart' }
[IO.File]::SetLastWriteTimeUtc($WatchdogStateFile, $now.AddMinutes(-4))
if (Test-WatchdogHeartbeatFresh -ProcessStartedAt $now.AddHours(-1) -Now $now) { throw 'Persistent heartbeat corruption was ignored' }
$script:owned = @($script:owned[0], $script:owned[0])
$ambiguous = $false
try { [void](Ensure-ManagedWatchdog) } catch { $ambiguous = $true }
if (-not $ambiguous -or $script:starts -ne 2) { throw 'Duplicate watchdogs triggered another launch' }
Remove-Item -LiteralPath $WatchdogGate
if ((Ensure-ManagedWatchdog) -ne 'disabled') { throw 'Manual stop was ignored' }
[pscustomobject]@{ starts=$script:starts; stops=$script:stops; disabled=$true; ambiguous=$ambiguous } | ConvertTo-Json -Compress
