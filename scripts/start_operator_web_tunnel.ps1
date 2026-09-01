param([int]$ProxyPort = 18767, [int]$TimeoutSeconds = 60)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Cloudflared = Join-Path $Root 'tools\cloudflared.exe'
$LogDir = Join-Path $Root 'logs'
$UrlPath = Join-Path $Root 'data\operator_web_tunnel_url.txt'
$TunnelLog = Join-Path $LogDir 'cloudflared-operator.log'
if (-not (Test-Path -LiteralPath $Cloudflared)) { throw "cloudflared not found: $Cloudflared" }
New-Item -ItemType Directory -Force -Path $LogDir, (Split-Path -Parent $UrlPath) | Out-Null
Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -like '*cloudflared*' -and $_.CommandLine -like "*$ProxyPort*") -or
    ($_.Name -like 'python*.exe' -and $_.CommandLine -like '*operator_web_proxy.py*')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Process -FilePath $Python -ArgumentList @('-u', (Join-Path $PSScriptRoot 'operator_web_proxy.py'), '--port', $ProxyPort) -WorkingDirectory $Root -RedirectStandardOutput (Join-Path $LogDir 'operator_web_proxy.out.log') -RedirectStandardError (Join-Path $LogDir 'operator_web_proxy.err.log') -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 1
Remove-Item -LiteralPath $TunnelLog -Force -ErrorAction SilentlyContinue
Start-Process -FilePath $Cloudflared -ArgumentList @('tunnel', '--url', "http://127.0.0.1:$ProxyPort", '--protocol', 'http2', '--no-autoupdate', '--logfile', $TunnelLog) -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
$deadline = (Get-Date).AddSeconds($TimeoutSeconds); $publicUrl = $null
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $TunnelLog) { $content = Get-Content -LiteralPath $TunnelLog -Raw -ErrorAction SilentlyContinue; if ($content -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $publicUrl = $Matches[0]; break } }
    Start-Sleep -Seconds 2
}
if (-not $publicUrl) { throw 'cloudflared did not produce an operator web URL within the timeout.' }
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($UrlPath, $publicUrl, $utf8)
Write-Output "Operator web tunnel ready: $publicUrl"
Write-Output 'Use #查重网页.'
