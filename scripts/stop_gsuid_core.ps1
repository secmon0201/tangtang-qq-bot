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
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped GsUID Core PID $($process.ProcessId)"
}
