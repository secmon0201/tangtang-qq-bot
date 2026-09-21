# Match the windowless launcher's captured-output decoder, including errors.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. (Join-Path $PSScriptRoot 'watchdog_lifecycle.ps1')
$lock = Enter-WatchdogLifecycleLock
try { Ensure-ManagedWatchdog | Write-Output }
finally { $lock.Dispose() }
