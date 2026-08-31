$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -like "python*.exe" -and
        $_.CommandLine -match 'gsuid_core[\\/]core\.py|gsuid_core\.core|run_gsuid_core\.py'
    }
if (-not $processes) {
    Write-Host "GsUID Core is not running."
    exit 0
}
foreach ($process in $processes) {
    if (-not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) {
        Write-Host "GsUID Core PID $($process.ProcessId) had already stopped."
        continue
    }
    try {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    } catch {
        if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
            throw
        }
        Write-Host "GsUID Core PID $($process.ProcessId) stopped with its parent process."
        continue
    }
    try {
        Wait-Process -Id $process.ProcessId -Timeout 15 -ErrorAction Stop
    } catch {
        if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
            throw "GsUID Core PID $($process.ProcessId) did not stop within 15 seconds."
        }
    }
    Write-Host "Stopped GsUID Core PID $($process.ProcessId)"
}
