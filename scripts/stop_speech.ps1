[CmdletBinding()]
param([switch]$PreserveEnabledState)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$speechRoot = Split-Path -Parent $PSScriptRoot
$speechData = Join-Path $speechRoot 'data\tts'
$speechPidPath = Join-Path $speechData 'service.pid'
$speechConfigPath = Join-Path $speechData 'service.json'
if (-not $PreserveEnabledState) {
    & (Join-Path $speechRoot '.venv\Scripts\python.exe') (Join-Path $speechRoot 'scripts\speech_switch.py') disable
    if ($LASTEXITCODE -ne 0) { throw 'Cannot close the speech gate; refusing to stop an automatically managed service.' }
}
if (-not (Test-Path -LiteralPath $speechConfigPath)) { return }
$speechConfig = Get-Content -LiteralPath $speechConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
$speechBootstrap = Join-Path $speechRoot 'bot\integrations\sovits_bootstrap.py'
$speechLock = [IO.File]::Open((Join-Path $speechData 'start.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    # Windows venv launchers spawn a second Python process. Match the owned
    # bootstrap and exact configuration so both stop, including an orphan child.
    $speechProcesses = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(?:3\.10)?\.exe$' -and $_.CommandLine -and
        $_.CommandLine.Contains($speechBootstrap) -and
        $_.CommandLine.Contains([string]$speechConfig.config)
    })
    foreach ($speechProcess in $speechProcesses) {
        Stop-Process -Id ([int]$speechProcess.ProcessId) -Force -ErrorAction SilentlyContinue
    }
    foreach ($speechProcess in $speechProcesses) {
        Wait-Process -Id ([int]$speechProcess.ProcessId) -Timeout 15 -ErrorAction SilentlyContinue
        $speechDeadline = (Get-Date).AddSeconds(15)
        do {
            $speechRemaining = Get-CimInstance Win32_Process -Filter "ProcessId=$($speechProcess.ProcessId)" -ErrorAction SilentlyContinue
            if (-not $speechRemaining) { break }
            Start-Sleep -Milliseconds 200
        } while ((Get-Date) -lt $speechDeadline)
        if ($speechRemaining) {
            throw "Speech process $($speechProcess.ProcessId) did not exit."
        }
    }
    if (Test-Path -LiteralPath $speechPidPath) { Remove-Item -LiteralPath $speechPidPath }
    Write-Output 'Independent speech runtime stopped; QQ and NoneBot unchanged.'
}
finally { $speechLock.Dispose() }
