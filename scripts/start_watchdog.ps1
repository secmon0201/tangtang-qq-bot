Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $root 'logs\qq-transport-watchdog.pid'
$watchdog = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'watch_qq_transport.ps1'))
if (Test-Path -LiteralPath $pidPath) {
    $rawPid = (Get-Content -LiteralPath $pidPath -Raw -ErrorAction SilentlyContinue).Trim()
    if ($rawPid -match '^\d+$') {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $rawPid" -ErrorAction SilentlyContinue
        if (
            $null -ne $process -and
            $process.Name -match '(?i)^(powershell|pwsh)\.exe$' -and
            [string]$process.CommandLine -match [regex]::Escape($watchdog)
        ) {
            Write-Output "QQ transport watchdog is already running with PID $rawPid."
            exit 0
        }
    }
}

& (Join-Path $PSScriptRoot 'stop_watchdog.ps1') | Out-Host
$engine = (Get-Command 'powershell.exe' -ErrorAction Stop).Source
$arguments = @(
    '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', $watchdog
)
$process = Start-Process -FilePath $engine -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru
for ($attempt = 0; $attempt -lt 30 -and -not (Test-Path -LiteralPath $pidPath); $attempt++) {
    Start-Sleep -Milliseconds 100
}
if (-not (Test-Path -LiteralPath $pidPath)) {
    if ($process.HasExited) { throw "QQ transport watchdog exited with code $($process.ExitCode)." }
    throw 'QQ transport watchdog did not create its PID file.'
}
Write-Output "QQ transport watchdog started with PID $($process.Id)."
