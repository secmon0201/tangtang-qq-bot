. (Join-Path $PSScriptRoot 'watchdog_lifecycle.ps1')
# Register before enabling recovery: scheduling failures must be visible.
Register-WatchdogSupervisor
$lock = Enter-WatchdogLifecycleLock
try {
    'enabled' | Set-Content -LiteralPath $WatchdogGate -Encoding ascii
    Ensure-ManagedWatchdog | Write-Output
} finally { $lock.Dispose() }
