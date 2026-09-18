. (Join-Path $PSScriptRoot 'watchdog_lifecycle.ps1')
$lock = Enter-WatchdogLifecycleLock
try { Ensure-ManagedWatchdog | Write-Output }
finally { $lock.Dispose() }
