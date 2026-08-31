$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "napcat_process.ps1")

$settings = Get-BotLaunchSettings -Root $Root
$launcher = Get-NapCatLauncherPath -NapCatDir $settings.NapCatDir

Write-Host "[1/6] Starting GsUID Core..."
& (Join-Path $PSScriptRoot "start_gsuid_core.ps1") -Background

Write-Host "[2/6] Starting the bot..."
& (Join-Path $PSScriptRoot "start.ps1")

$namedTunnelConfig = Join-Path $Root 'data\cloudflared\tangtang-web.yml'
if (Test-Path -LiteralPath $namedTunnelConfig) {
    Write-Host "[3/6] Starting the fixed Tangtang web tunnel..."
    & (Join-Path $PSScriptRoot "start_tangtang_named_tunnel.ps1")
    Write-Host "[4/6] Announcement, operator, and NTE paths share the fixed tunnel."
    Write-Host "[5/6] Legacy Quick Tunnels were skipped."
} else {
    Write-Host "[3/6] Starting the announcement web tunnel..."
    & (Join-Path $PSScriptRoot "start_global_announcement_tunnel.ps1")

    Write-Host "[4/6] Starting the operator web tunnel..."
    & (Join-Path $PSScriptRoot "start_operator_web_tunnel.ps1")

    Write-Host "[5/6] No additional feature tunnel is required."
}

$existingRoots = @(Get-QqRootProcesses)
$tracked = $null
if ($existingRoots.Count -gt 0) {
    $tracked = Get-VerifiedStateRoot -Roots $existingRoots -StatePath $settings.StatePath -AccountId $settings.AccountId
}
if ($null -ne $tracked) {
    Write-Host "[6/6] NapCat QQ is already tracked (PID $($tracked.Pid))."
    exit 0
}

$connectedRoots = @(Get-QqRootsConnectedToPort -Port $settings.Port)
if ($connectedRoots.Count -eq 1) {
    Save-QuickNapCatState -StatePath $settings.StatePath -Root $connectedRoots[0] -AccountId $settings.AccountId
    Write-Host "[6/6] Found an existing NapCat OneBot connection (QQ PID $($connectedRoots[0].Pid))."
    exit 0
}

$accountRoots = @($existingRoots | Where-Object { $_.AccountIds -contains $settings.AccountId })
if ($accountRoots.Count -eq 1) {
    Save-QuickNapCatState -StatePath $settings.StatePath -Root $accountRoots[0] -AccountId $settings.AccountId
    Write-Host "[6/6] Found an existing QQ process with the configured account hint (PID $($accountRoots[0].Pid))."
    exit 0
}

if ($existingRoots.Count -gt 0) {
    Write-Host "[6/6] Existing QQ processes were not identified as NapCat. Starting the configured NapCat account."
} else {
    Write-Host "[6/6] Starting NapCat for QQ $($settings.AccountId)..."
}

$startedAt = Get-Date
$startedAtTicks = $startedAt.AddSeconds(-3).ToUniversalTime().Ticks
$launcherCommand = '""{0}" -q {1}"' -f $launcher, $settings.AccountId
Start-Process -FilePath $env:ComSpec -ArgumentList @("/d", "/c", $launcherCommand) -WorkingDirectory $settings.NapCatDir | Out-Null

$trackedRoot = $null
$existingRootPids = @($existingRoots | ForEach-Object { [int]$_.Pid })
for ($attempt = 0; $attempt -lt 8 -and $null -eq $trackedRoot; $attempt++) {
    Start-Sleep -Seconds 2
    $candidates = @(Get-QqRootProcesses | Where-Object {
        $_.Pid -notin $existingRootPids -and
        $_.CreationTicks -ge $startedAtTicks
    })
    if ($candidates.Count -gt 0) {
        $trackedRoot = $candidates | Sort-Object CreationTicks -Descending | Select-Object -First 1
    }
}

if ($null -ne $trackedRoot) {
    Save-QuickNapCatState -StatePath $settings.StatePath -Root $trackedRoot -AccountId $settings.AccountId
    Write-Host "NapCat QQ started (PID $($trackedRoot.Pid)). Complete any QQ scan or verification in the NapCat/QQ window."
} else {
    Write-Warning "NapCat was launched, but no new QQ root process was found yet. Complete any UAC or QQ login prompt, then rerun this file only if NapCat did not open."
}
