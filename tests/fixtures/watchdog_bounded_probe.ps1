param(
    [string]$WatchdogPath = "",
    [string]$ChildPidPath = "",
    [switch]$ChildMode
)

$ErrorActionPreference = "Stop"

if ($ChildMode) {
    $engine = (Get-Process -Id $PID -ErrorAction Stop).Path
    $child = Start-Process -FilePath $engine `
        -ArgumentList @("-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 30") `
        -WindowStyle Hidden -PassThru
    $child.Id | Set-Content -LiteralPath $ChildPidPath -Encoding ascii
    exit 0
}

. $WatchdogPath -LibraryOnly
$timer = [Diagnostics.Stopwatch]::StartNew()
$result = Invoke-BoundedPowerShellScript `
    -ScriptPath $PSCommandPath `
    -TimeoutSeconds 5 `
    -ScriptArguments @("-ChildMode", "-ChildPidPath", $ChildPidPath)
$timer.Stop()

[pscustomobject]@{
    Outcome = $result.Outcome
    ExitCode = $result.ExitCode
    ElapsedMilliseconds = $timer.ElapsedMilliseconds
} | ConvertTo-Json -Compress
