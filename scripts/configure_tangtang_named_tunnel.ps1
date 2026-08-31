param(
    [string]$Hostname = 'tangtang.secmon.cn',
    [string]$ShortHostname = 's.secmon.cn',
    [string]$TunnelName = 'tangtang-secmon'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $Root 'tools\cloudflared.exe'
$RuntimeDir = Join-Path $Root 'data\cloudflared'
$ConfigPath = Join-Path $RuntimeDir 'tangtang-web.yml'
$CertificatePath = Join-Path $env:USERPROFILE '.cloudflared\cert.pem'

if (-not (Test-Path -LiteralPath $Cloudflared)) {
    throw "cloudflared not found: $Cloudflared"
}
foreach ($candidateHostname in @($Hostname, $ShortHostname)) {
    if ($candidateHostname -notmatch '^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$') {
        throw 'Hostnames must be plain DNS hostnames.'
    }
}
if (-not (Test-Path -LiteralPath $CertificatePath)) {
    Write-Output 'Cloudflare authorization is required. Complete the browser authorization opened by cloudflared.'
    & $Cloudflared tunnel login
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0 -or -not (Test-Path -LiteralPath $CertificatePath)) {
        throw 'Cloudflare tunnel login did not complete.'
    }
}

$tunnelJson = & $Cloudflared tunnel list --output json
$nativeExitCode = $LASTEXITCODE
if ($nativeExitCode -ne 0) { throw "cloudflared tunnel list failed with code $nativeExitCode." }
$tunnels = @($tunnelJson | ConvertFrom-Json)
$tunnel = @($tunnels | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1)
if ($tunnel.Count -eq 0) {
    & $Cloudflared tunnel create $TunnelName
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0) { throw "cloudflared tunnel create failed with code $nativeExitCode." }
    $tunnelJson = & $Cloudflared tunnel list --output json
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0) { throw "cloudflared tunnel list failed with code $nativeExitCode." }
    $tunnels = @($tunnelJson | ConvertFrom-Json)
    $tunnel = @($tunnels | Where-Object { $_.name -eq $TunnelName } | Select-Object -First 1)
}
if ($tunnel.Count -ne 1) { throw "Named tunnel was not found after creation: $TunnelName" }

$TunnelId = [string]$tunnel[0].id
$CredentialsPath = Join-Path $env:USERPROFILE ".cloudflared\$TunnelId.json"
if (-not (Test-Path -LiteralPath $CredentialsPath)) {
    throw "Tunnel credentials were not found: $CredentialsPath"
}

foreach ($candidateHostname in @($Hostname, $ShortHostname)) {
    & $Cloudflared tunnel route dns --overwrite-dns $TunnelId $candidateHostname
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -ne 0) {
        throw "cloudflared tunnel route dns failed for $candidateHostname with code $nativeExitCode."
    }
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
$yamlCredentials = $CredentialsPath.Replace("'", "''")
$yaml = @(
    "tunnel: $TunnelId"
    "credentials-file: '$yamlCredentials'"
    'ingress:'
    "  - hostname: $Hostname"
    '    service: http://127.0.0.1:18769'
    "  - hostname: $ShortHostname"
    '    service: http://127.0.0.1:18769'
    '  - service: http_status:404'
) -join "`r`n"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($ConfigPath, $yaml + "`r`n", $utf8)

Write-Output "Named tunnel configured for https://$Hostname"
Write-Output "Short-link host configured for https://$ShortHostname"
Write-Output "Runtime config: $ConfigPath"
Write-Output 'Run scripts\start_tangtang_named_tunnel.ps1 after the domain DNS is active.'
