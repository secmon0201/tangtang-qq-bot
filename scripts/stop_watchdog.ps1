. (Join-Path $PSScriptRoot 'watchdog_lifecycle.ps1')
$lock = Enter-WatchdogLifecycleLock
try {
    # Save operator intent before stopping; recurring checks remain inert.
    Remove-Item -LiteralPath $WatchdogGate -Force -ErrorAction SilentlyContinue
    Stop-OwnedWatchdogProcesses
    Write-Output 'Watchdog stopped; automatic recovery disabled.'
} finally { $lock.Dispose() }
