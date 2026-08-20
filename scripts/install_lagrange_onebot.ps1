param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $root 'downloads\Lagrange.OneBot_win-x64_net9.0_SelfContained.zip'
$destination = Join-Path $root 'Lagrange.OneBot'
$expectedHash = 'F49D7351EE37F4985D3B90383A727F5FE25A2FBA1265B7153822BA5702291025'
$expectedLength = 34989556L

if (-not (Test-Path -LiteralPath $archive)) {
    throw "Lagrange archive is missing: $archive"
}
$item = Get-Item -LiteralPath $archive
if ($item.Length -ne $expectedLength) {
    throw "Lagrange archive is incomplete (expected $expectedLength bytes, got $($item.Length))."
}
$actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
if ($actualHash -ne $expectedHash) {
    throw 'Lagrange archive SHA-256 verification failed. Refusing to extract it.'
}
if (Test-Path -LiteralPath $destination) {
    if (-not $Force) {
        throw "Destination already exists: $destination. Use -Force only after reviewing it."
    }
    $existingExe = Get-ChildItem -LiteralPath $destination -Filter 'Lagrange.OneBot.exe' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $existingExe) {
        throw "Existing destination is not a recognized Lagrange.OneBot installation: $destination"
    }
    Write-Output "Verified installation already present: $($existingExe.FullName)"
    exit 0
}

Expand-Archive -LiteralPath $archive -DestinationPath $destination -ErrorAction Stop
$executable = Get-ChildItem -LiteralPath $destination -Filter 'Lagrange.OneBot.exe' -Recurse -File | Select-Object -First 1
if ($null -eq $executable) {
    throw 'Extraction completed but Lagrange.OneBot.exe was not found.'
}
Write-Output "Installed verified Lagrange.OneBot to: $($executable.DirectoryName)"
