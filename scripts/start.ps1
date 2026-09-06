param(
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LogDir = Join-Path $Root "logs"
$PidFile = Join-Path $LogDir "bot.pid"
$LifecycleLog = Join-Path $LogDir "bot.lifecycle.log"

# Redirected Python streams otherwise inherit the Windows legacy code page.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONFAULTHANDLER = "1"

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

function Archive-PreviousBotLogs {
    param(
        [Parameter(Mandatory)][string[]]$Paths,
        [Parameter(Mandatory)][string]$HistoryRoot,
        [ValidateRange(1, 100)][int]$KeepRuns = 20
    )

    $existing = @($Paths | Where-Object { Test-Path -LiteralPath $_ })
    if ($existing.Count -eq 0) {
        return $null
    }

    $archiveDir = Join-Path $HistoryRoot (Get-Date).ToString("yyyyMMdd-HHmmss-fff")
    New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
    foreach ($path in $existing) {
        $destination = Join-Path $archiveDir (Split-Path -Leaf $path)
        $moved = $false
        for ($attempt = 0; $attempt -lt 60 -and -not $moved; $attempt++) {
            try {
                Move-Item -LiteralPath $path -Destination $destination -Force -ErrorAction Stop
                $moved = $true
            } catch {
                if ($attempt -eq 59) { throw }
                Start-Sleep -Milliseconds 250
            }
        }
    }

    $expired = @(Get-ChildItem -LiteralPath $HistoryRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -Skip $KeepRuns)
    foreach ($directory in $expired) {
        Remove-Item -LiteralPath $directory.FullName -Recurse -Force
    }
    return $archiveDir
}

if ($Foreground) {
    Push-Location $Root
    try { & $Python -m bot } finally { Pop-Location }
    exit $LASTEXITCODE
}

$stdout = Join-Path $LogDir "bot.out.log"
$stderr = Join-Path $LogDir "bot.err.log"
$history = Join-Path $LogDir "history"
$archive = Archive-PreviousBotLogs -Paths @($stdout, $stderr) -HistoryRoot $history
if ($archive) {
    Add-Content -LiteralPath $LifecycleLog -Encoding utf8 -Value ("{0} logs_archived path={1}" -f (Get-Date).ToString("o"), $archive)
}
$process = Start-Process -FilePath $Python -ArgumentList @("-u", "-X", "faulthandler", "-m", "bot") -WorkingDirectory $Root -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
$process.Id | Set-Content -LiteralPath $PidFile -Encoding ascii
Add-Content -LiteralPath $LifecycleLog -Encoding utf8 -Value ("{0} bot_started pid={1}" -f (Get-Date).ToString("o"), $process.Id)
Write-Output "Bot started with PID $($process.Id). Logs: $LogDir"
