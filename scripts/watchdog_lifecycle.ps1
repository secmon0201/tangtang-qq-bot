# Shared lifecycle for the watchdog and its independent Windows scheduled check.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$WatchdogRoot = Split-Path -Parent $PSScriptRoot
$WatchdogScript = Join-Path $PSScriptRoot 'watch_qq_transport.ps1'
$WatchdogGate = Join-Path $WatchdogRoot 'data\watchdog_enabled.flag'
$WatchdogPidFile = Join-Path $WatchdogRoot 'logs\qq-transport-watchdog.pid'
$WatchdogStateFile = Join-Path $WatchdogRoot 'data\qq-transport-watchdog-state.json'

function Get-WatchdogTaskName {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($WatchdogRoot.ToLowerInvariant()))).Replace('-', '').Substring(0, 12) }
    finally { $sha.Dispose() }
    return "QQBot-Watchdog-$hash"
}

function Enter-WatchdogLifecycleLock {
    New-Item -ItemType Directory -Force -Path (Join-Path $WatchdogRoot 'data') | Out-Null
    $lockPath = Join-Path $WatchdogRoot 'data\watchdog-lifecycle.lock'
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try { return [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None) }
        catch [IO.IOException] { Start-Sleep -Milliseconds 250 }
    }
    throw 'Another watchdog lifecycle operation is still running.'
}

function Get-OwnedWatchdogProcesses {
    $pattern = [regex]::Escape([IO.Path]::GetFullPath($WatchdogScript))
    return @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" -OperationTimeoutSec 5 |
        Where-Object { $_.CommandLine -and $_.CommandLine -match ('(?i)-File\s+"?' + $pattern + '"?(?:\s|$)') })
}

function Test-WatchdogHeartbeatFresh {
    param([Parameter(Mandatory)][datetime]$ProcessStartedAt, [datetime]$Now = [DateTime]::UtcNow)
    # Allow a new watcher to finish its first bounded recovery cycle.
    if (($Now.ToUniversalTime() - $ProcessStartedAt.ToUniversalTime()).TotalSeconds -lt 180) { return $true }
    try {
        $state = Get-Content -LiteralPath $WatchdogStateFile -Raw -Encoding utf8 | ConvertFrom-Json
        $completed = [DateTime]::Parse($state.last_check_completed_at).ToUniversalTime()
        $age = ($Now.ToUniversalTime() - $completed).TotalSeconds
        return $age -ge 0 -and $age -le 180
    } catch {
        # The watcher may be replacing the JSON while we read it. Give a fresh
        # write time to finish; persistent corruption still expires after 180s.
        $file = Get-Item -LiteralPath $WatchdogStateFile -ErrorAction SilentlyContinue
        if ($null -eq $file) { return $false }
        $writeAge = ($Now.ToUniversalTime() - $file.LastWriteTimeUtc).TotalSeconds
        return $writeAge -ge 0 -and $writeAge -le 180
    }
}

function Stop-OwnedWatchdogProcesses {
    foreach ($process in @(Get-OwnedWatchdogProcesses)) {
        $live = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        if ($null -ne $live -and [Math]::Abs(($live.StartTime - $process.CreationDate).TotalSeconds) -lt 1) {
            $live.Kill()
            if (-not $live.WaitForExit(5000)) { throw 'Watchdog did not exit.' }
        }
    }
    Remove-Item -LiteralPath $WatchdogPidFile -Force -ErrorAction SilentlyContinue
}

function Start-DetachedWatchdog {
    $engine = (Get-Command powershell.exe -ErrorAction Stop).Source
    $commandLine = '"{0}" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{1}"' -f $engine, $WatchdogScript
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    # WMI owns the launch, outside the terminal/agent/task process tree.
    $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -OperationTimeoutSec 10 -Arguments @{
        CommandLine = $commandLine; CurrentDirectory = $WatchdogRoot; ProcessStartupInformation = $startup
    }
    if ($created.ReturnValue -ne 0) { throw "Detached watchdog launch failed: $($created.ReturnValue)" }
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        if ((Test-Path -LiteralPath $WatchdogPidFile) -and
            (Get-Content -LiteralPath $WatchdogPidFile -Raw).Trim() -eq [string]$created.ProcessId) {
            return [int]$created.ProcessId
        }
        Start-Sleep -Milliseconds 100
    }
    throw 'Detached watchdog did not create its PID file.'
}

function Ensure-ManagedWatchdog {
    if (-not (Test-Path -LiteralPath $WatchdogGate)) { return 'disabled' }
    $processes = @(Get-OwnedWatchdogProcesses)
    if ($processes.Count -gt 1) { throw 'Multiple owned watchdogs require operator inspection.' }
    if ($processes.Count -eq 1 -and (Test-WatchdogHeartbeatFresh -ProcessStartedAt $processes[0].CreationDate)) {
        $processes[0].ProcessId | Set-Content -LiteralPath $WatchdogPidFile -Encoding ascii
        return 'healthy'
    }
    Stop-OwnedWatchdogProcesses
    $startedId = Start-DetachedWatchdog
    $log = Join-Path $WatchdogRoot 'logs\watchdog-supervisor.log'
    Add-Content -LiteralPath $log -Encoding utf8 -Value ('{0} watchdog_restarted pid={1}' -f [DateTime]::UtcNow.ToString('o'), $startedId)
    return "started pid=$startedId"
}

function Register-WatchdogSupervisor {
    # powershell.exe can flash a console before processing -WindowStyle Hidden.
    # pythonw is a GUI-subsystem executable; its child uses CREATE_NO_WINDOW.
    $engine = Join-Path $WatchdogRoot '.venv\Scripts\pythonw.exe'
    $scriptPath = Join-Path $PSScriptRoot 'run_watchdog_check.py'
    foreach ($required in @($engine, $scriptPath)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing windowless watchdog launcher: $required" }
    }
    $action = New-ScheduledTaskAction -Execute $engine -Argument ('"{0}"' -f $scriptPath) -WorkingDirectory $WatchdogRoot
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $triggers = @(
        New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
        New-ScheduledTaskTrigger -AtLogOn -User $user
    )
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 50) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName (Get-WatchdogTaskName) -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description 'Restore this local QQ bot watchdog only while its saved operator gate is enabled.' -Force | Out-Null
}
