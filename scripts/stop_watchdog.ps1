$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$PidPath = Join-Path $Root "logs\napcat-watchdog.pid"
$WatchScript = [regex]::Escape([IO.Path]::GetFullPath((Join-Path $PSScriptRoot "watch_napcat.ps1")))

if (-not (Test-Path -LiteralPath $PidPath)) {
    Write-Output "NapCat watchdog is not running."
    exit 0
}

$rawPid = (Get-Content -LiteralPath $PidPath -Raw -ErrorAction SilentlyContinue).Trim()
if ($rawPid -notmatch "^\d+$") {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Output "Removed invalid NapCat watchdog PID file."
    exit 0
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId = $rawPid" -ErrorAction SilentlyContinue
if ($null -ne $process -and $process.Name -match "(?i)^(powershell|pwsh)\.exe$" -and [string]$process.CommandLine -match $WatchScript) {
    Stop-Process -Id ([int]$rawPid) -Force
    Write-Output "Stopped NapCat watchdog PID $rawPid"
} else {
    Write-Output "NapCat watchdog PID file was stale; no unrelated process was stopped."
}
Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
