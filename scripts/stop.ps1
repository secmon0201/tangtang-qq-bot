$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root "logs\bot.pid"
$LifecycleLog = Join-Path $Root "logs\bot.lifecycle.log"

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$pythonPattern = [regex]::Escape([IO.Path]::GetFullPath($Python))
$botProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -match $pythonPattern -and
    $_.CommandLine -match "(?i)(-m\s+bot|bot\.__main__)"
})

if ($botProcesses.Count -eq 0) {
    Write-Output "Bot process not found."
} else {
    foreach ($process in $botProcesses) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
        Add-Content -LiteralPath $LifecycleLog -Encoding utf8 -Value ("{0} bot_stop_requested pid={1}" -f (Get-Date).ToString("o"), $process.ProcessId)
        Write-Output "Stopped bot PID $($process.ProcessId)"
    }
}

if (Test-Path -LiteralPath $PidFile) {
    Remove-Item -LiteralPath $PidFile -Force
}
