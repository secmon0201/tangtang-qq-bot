Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'napcat_process.ps1')
$settings = Get-BotLaunchSettings -Root $root
$launcher = Get-NapCatLauncherPath -NapCatDir $settings.NapCatDir
$existingRoots = @(Get-QqRootProcesses)
$tracked = if ($existingRoots.Count -gt 0) {
    Get-VerifiedStateRoot -Roots $existingRoots -StatePath $settings.StatePath -AccountId $settings.AccountId
} else { $null }
if ($null -ne $tracked) {
    Write-Output "NapCat QQ is already tracked (PID $($tracked.Pid))."
    exit 0
}
$connectedRoots = @(Get-QqRootsConnectedToPort -Port $settings.Port)
if ($connectedRoots.Count -eq 1) {
    Save-QuickNapCatState -StatePath $settings.StatePath -Root $connectedRoots[0] -AccountId $settings.AccountId
    Write-Output "Found an existing NapCat OneBot connection (QQ PID $($connectedRoots[0].Pid))."
    exit 0
}
$accountRoots = @($existingRoots | Where-Object { $_.AccountIds -contains $settings.AccountId })
if ($accountRoots.Count -eq 1) {
    Save-QuickNapCatState -StatePath $settings.StatePath -Root $accountRoots[0] -AccountId $settings.AccountId
    Write-Output "Found an existing QQ process with the configured account hint (PID $($accountRoots[0].Pid))."
    exit 0
}

$startedAtTicks = (Get-Date).AddSeconds(-3).ToUniversalTime().Ticks
$launcherCommand = '""{0}" -q {1}"' -f $launcher, $settings.AccountId
Start-Process -FilePath $env:ComSpec -ArgumentList @('/d', '/c', $launcherCommand) -WorkingDirectory $settings.NapCatDir | Out-Null
$trackedRoot = $null
$existingRootPids = @($existingRoots | ForEach-Object { [int]$_.Pid })
for ($attempt = 0; $attempt -lt 8 -and $null -eq $trackedRoot; $attempt++) {
    Start-Sleep -Seconds 2
    $trackedRoot = Get-QqRootProcesses | Where-Object {
        $_.Pid -notin $existingRootPids -and $_.CreationTicks -ge $startedAtTicks
    } | Sort-Object CreationTicks -Descending | Select-Object -First 1
}
if ($null -ne $trackedRoot) {
    Save-QuickNapCatState -StatePath $settings.StatePath -Root $trackedRoot -AccountId $settings.AccountId
    Write-Output "NapCat QQ started (PID $($trackedRoot.Pid)). Complete any QQ verification manually."
} else {
    Write-Warning 'NapCat was launched, but no new QQ root process was found yet.'
}
