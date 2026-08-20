param(
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"
$PidFile = Join-Path $LogDir "bot.pid"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run: python -m venv .venv"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$pythonPattern = [regex]::Escape([IO.Path]::GetFullPath($Python))
$botProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -match $pythonPattern -and
    $_.CommandLine -match "(?i)(-m\s+bot|bot\.__main__)"
})

if ($botProcesses.Count -gt 0) {
    $runningPid = [int]$botProcesses[0].ProcessId
    $runningPid | Set-Content -LiteralPath $PidFile -Encoding ascii
    Write-Output "Bot is already running with PID $runningPid"
    exit 0
}

if ($Foreground) {
    Push-Location $Root
    try { & $Python -m bot } finally { Pop-Location }
    exit $LASTEXITCODE
}

$stdout = Join-Path $LogDir "bot.out.log"
$stderr = Join-Path $LogDir "bot.err.log"
$process = Start-Process -FilePath $Python -ArgumentList @("-m", "bot") -WorkingDirectory $Root -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
$process.Id | Set-Content -LiteralPath $PidFile -Encoding ascii
Write-Output "Bot started with PID $($process.Id). Logs: $LogDir"
