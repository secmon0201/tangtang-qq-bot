$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "napcat_process.ps1")

Write-Host "[0/5] Stopping the NapCat watchdog..."
& (Join-Path $PSScriptRoot "stop_watchdog.ps1")

Write-Host "[1/5] Stopping the NTE login tunnel without changing its enabled state..."
& (Join-Path $PSScriptRoot "stop_nte_tunnel.ps1") -PreserveGuardState

$settings = Get-BotLaunchSettings -Root $Root
$roots = @(Get-QqRootProcesses)
$targets = @{}

$tracked = $null
if ($roots.Count -gt 0) {
    $tracked = Get-VerifiedStateRoot -Roots $roots -StatePath $settings.StatePath -AccountId $settings.AccountId
}
if ($null -ne $tracked) {
    $targets[$tracked.Pid] = "quick-launch record"
}
foreach ($qqRoot in $roots | Where-Object { $_.AccountIds -contains $settings.AccountId }) {
    $targets[$qqRoot.Pid] = "configured account hint"
}
foreach ($qqRoot in Get-QqRootsConnectedToPort -Port $settings.Port) {
    $targets[$qqRoot.Pid] = "active OneBot connection"
}

if ($targets.Count -eq 0) {
    Write-Host "[2/5] No NapCat QQ process could be safely identified. No ordinary QQ process was closed."
} else {
    Write-Host "[2/5] Stopping verified NapCat QQ process trees..."
    foreach ($target in $targets.GetEnumerator()) {
        Write-Host "  PID $($target.Key) ($($target.Value))"
        if (-not (Stop-VerifiedProcessTree -ProcessId ([int]$target.Key))) {
            Write-Warning "PID $($target.Key) is still running. Run this file as administrator and approve the UAC prompt."
        }
    }
}

Start-Sleep -Seconds 1
$remainingRoots = @(Get-QqRootProcesses)
$remainingTargets = @($targets.Keys | Where-Object { $remainingRoots.Pid -contains [int]$_ })
if ($remainingTargets.Count -eq 0 -and (Test-Path -LiteralPath $settings.StatePath)) {
    Remove-Item -LiteralPath $settings.StatePath -Force
}

Write-Host "[3/5] Stopping the bot..."
& (Join-Path $PSScriptRoot "stop.ps1")

Write-Host "[4/5] Stopping GsUID Core..."
& (Join-Path $PSScriptRoot "stop_gsuid_core.ps1")

Write-Host "[5/5] Verifying process state..."

$pythonPath = [regex]::Escape((Join-Path $Root ".venv\Scripts\python.exe"))
$botResidual = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match $pythonPath -and $_.CommandLine -match "(?i)(-m\s+bot|bot\.__main__)"
})
if ($botResidual.Count -eq 0) {
    Write-Host "Bot process check: clear."
} else {
    Write-Warning "Bot process check found remaining PID(s): $($botResidual.ProcessId -join ', ')."
}

if ($remainingTargets.Count -eq 0) {
    Write-Host "NapCat process check: all verified target processes are closed."
} else {
    Write-Warning "NapCat process check found remaining target PID(s): $($remainingTargets -join ', ')."
}

$otherQq = @(Get-QqRootProcesses)
if ($otherQq.Count -gt 0) {
    Write-Host "Other QQ root PID(s) still running and intentionally not closed: $($otherQq.Pid -join ', ')."
}
