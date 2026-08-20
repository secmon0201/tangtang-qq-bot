param(
    [switch]$Foreground
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'configure_lagrange_onebot.ps1')

$envPath = Join-Path $root '.env'
$lagrangeDir = 'Lagrange.OneBot'
foreach ($line in Get-Content -LiteralPath $envPath -Encoding utf8) {
    if ($line -match '^\s*LAGRANGE_DIR\s*=\s*(.+?)\s*$') { $lagrangeDir = $Matches[1].Trim('"', "'") }
}
if (-not [IO.Path]::IsPathRooted($lagrangeDir)) { $lagrangeDir = Join-Path $root $lagrangeDir }
$executable = Get-ChildItem -LiteralPath $lagrangeDir -Filter 'Lagrange.OneBot.exe' -Recurse -File | Select-Object -First 1
if ($null -eq $executable) { throw "Lagrange.OneBot.exe not found under: $lagrangeDir" }

$existing = @(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -eq $executable.FullName
})
if ($existing.Count -gt 0) {
    Write-Output "Lagrange.OneBot is already running with PID $($existing[0].ProcessId)."
    exit 0
}
if ($Foreground) {
    Push-Location $executable.DirectoryName
    try { & $executable.FullName } finally { Pop-Location }
    exit $LASTEXITCODE
}

# The local compatibility build tolerates the lack of a Windows console. Its
# output is retained here; QQ login still requires a user to scan qr-<uin>.png.
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir 'lagrange.out.log'
$stderr = Join-Path $logDir 'lagrange.err.log'
$process = Start-Process -FilePath $executable.FullName -WorkingDirectory $executable.DirectoryName -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
$statePath = Join-Path $root 'data\quick-lagrange-state.json'
[pscustomobject]@{ pid = $process.Id; executable = $executable.FullName; started_at = [DateTime]::UtcNow.ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
Write-Output "Lagrange.OneBot started (PID $($process.Id)). Scan the generated qr-<QQ>.png manually if login is required."
