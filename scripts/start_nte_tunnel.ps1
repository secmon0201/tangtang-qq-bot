param(
    [int]$ProxyPort = 18765,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ToolsDir = Join-Path $Root "tools"
$Cloudflared = Join-Path $ToolsDir "cloudflared.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$CoreDir = Join-Path $Root "GsUID.Core"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $ToolsDir, $LogDir | Out-Null

$TunnelLog = Join-Path $LogDir "cloudflared-nte.log"
$ProxyOut = Join-Path $LogDir "nte_login_proxy.out.log"
$ProxyErr = Join-Path $LogDir "nte_login_proxy.err.log"

function Install-Cloudflared {
    if (Test-Path -LiteralPath $Cloudflared) {
        Write-Output "cloudflared found: $Cloudflared"
        return
    }
    Write-Output "Downloading cloudflared for Windows (one-time)..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Remove-Item -LiteralPath $Cloudflared -Force -ErrorAction SilentlyContinue
    $downloaded = $false
    # Preferred path: resolve the release asset through the GitHub API, which
    # redirects to the reachable asset host. This works behind regional blocks
    # that reset github.com itself.
    try {
        $releaseResponse = Invoke-WebRequest -Uri "https://api.github.com/repos/cloudflare/cloudflared/releases/latest" `
            -UseBasicParsing -TimeoutSec 30
        $release = $releaseResponse.Content | ConvertFrom-Json
        $asset = $release.assets | Where-Object { $_.name -eq "cloudflared-windows-amd64.exe" } | Select-Object -First 1
        if ($null -ne $asset) {
            Invoke-WebRequest -Uri ("https://api.github.com/repos/cloudflare/cloudflared/releases/assets/" + $asset.id) `
                -Headers @{ Accept = "application/octet-stream" } -OutFile $Cloudflared -UseBasicParsing -TimeoutSec 180
            $downloaded = $true
        }
    } catch {
        Write-Warning "GitHub API download failed, falling back to the direct release URL: $($_.Exception.Message)"
    }
    if (-not $downloaded) {
        Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
            -OutFile $Cloudflared -UseBasicParsing -TimeoutSec 180
    }
    Write-Output "cloudflared installed: $Cloudflared"
}

function Stop-ManagedProcesses {
    $tunnel = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like "*cloudflared*" -and $_.CommandLine -like "*$ProxyPort*"
    })
    foreach ($process in $tunnel) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped previous cloudflared PID $($process.ProcessId)"
    }
    $proxy = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like "python*.exe" -and $_.CommandLine -like "*nte_login_proxy.py*"
    })
    foreach ($process in $proxy) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped previous login proxy PID $($process.ProcessId)"
    }
    Start-Sleep -Milliseconds 800
}

function Test-CoreUp {
    try {
        $request = Invoke-WebRequest -Uri "http://127.0.0.1:8765/nte/i/__nte_health_check__" -UseBasicParsing -TimeoutSec 8
        return $request.StatusCode -eq 404
    } catch {
        if ($null -ne $_.Exception.Response) {
            return ([int]$_.Exception.Response.StatusCode -eq 404)
        }
        return $false
    }
}

function Start-CoreIfNeeded {
    if (Test-CoreUp) { return }
    Write-Output "GsUID Core is not answering; starting it..."
    & (Join-Path $PSScriptRoot "start_gsuid_core.ps1") -Background
}

function Restart-Core {
    & (Join-Path $PSScriptRoot "stop_gsuid_core.ps1")
    Start-Sleep -Seconds 2
    & (Join-Path $PSScriptRoot "start_gsuid_core.ps1") -Background
    Write-Output "GsUID Core restarted and answering."
}

$mutex = New-Object System.Threading.Mutex($false, "Local\NteTunnelStart")
if (-not $mutex.WaitOne(0)) {
    Write-Output "start_nte_tunnel.ps1 is already running in another window; skipping this run."
    exit 0
}
Remove-Item -LiteralPath (Join-Path $Root "data\nte_tunnel_disabled.flag") -Force -ErrorAction SilentlyContinue

Install-Cloudflared
Stop-ManagedProcesses
Start-CoreIfNeeded

Write-Output "Starting the path-restricted login proxy on 127.0.0.1:$ProxyPort..."
Start-Process -FilePath $Python -ArgumentList @("-u", (Join-Path $PSScriptRoot "nte_login_proxy.py")) `
    -WorkingDirectory $Root -RedirectStandardOutput $ProxyOut -RedirectStandardError $ProxyErr `
    -WindowStyle Hidden | Out-Null
Start-Sleep -Seconds 1

Write-Output "Starting cloudflared quick tunnel to the proxy..."
Remove-Item -LiteralPath $TunnelLog -Force -ErrorAction SilentlyContinue
Start-Process -FilePath $Cloudflared `
    -ArgumentList @("tunnel", "--url", "http://127.0.0.1:$ProxyPort", "--no-autoupdate", "--logfile", $TunnelLog) `
    -WorkingDirectory $Root -WindowStyle Hidden | Out-Null

$publicUrl = $null
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $TunnelLog) {
        $content = Get-Content -LiteralPath $TunnelLog -Raw -ErrorAction SilentlyContinue
        if ($content -match "https://[a-z0-9-]+\.trycloudflare\.com") {
            $publicUrl = $Matches[0]
            break
        }
    }
    Start-Sleep -Seconds 2
}
if (-not $publicUrl) {
    throw "cloudflared did not produce a trycloudflare.com URL within ${TimeoutSeconds}s. Check: $TunnelLog"
}
Write-Output "Tunnel URL: $publicUrl"

$ready = $false
$probeToken = "__nte_health_check__"
# Quick Tunnel DNS/edge propagation can lag behind URL creation by more than
# the first few probes, so keep the public check alive for up to two minutes.
for ($attempt = 0; $attempt -lt 24; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri "$publicUrl/nte/i/$probeToken" -UseBasicParsing -TimeoutSec 20
        if ($response.StatusCode -eq 404) {
            $ready = $true
            break
        }
    } catch {
        $statusCode = $null
        if ($null -ne $_.Exception.Response) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -eq 404) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 5
    }
}
if (-not $ready) {
    throw "The tunnel URL did not expose the NTE login route at $publicUrl (Core or tunnel is not ready)"
}

$blocked = $false
try {
    Invoke-WebRequest -Uri "$publicUrl/ws/QQLocalDataBot" -UseBasicParsing -TimeoutSec 20 | Out-Null
} catch {
    if ($_.Exception.Response.StatusCode -eq 404) {
        $blocked = $true
    }
}
if (-not $blocked) {
    Write-Warning "Path restriction check failed: non-/nte path did not return 404. Check the proxy."
} else {
    Write-Output "Path restriction verified: only /nte/* is exposed."
}

Write-Output "Writing NTELoginUrl..."
& (Join-Path $PSScriptRoot "set_nte_login_url.ps1") -PublicBaseUrl $publicUrl

Write-Output "Restarting GsUID Core so the new NTELoginUrl takes effect..."
Restart-Core

Write-Output ""
Write-Output "Done. Public login base URL: $publicUrl"
Write-Output "Group members can now use: #nte登录"
Write-Output "After every reboot, tunnel restart, or address change, run this script again:"
Write-Output "  .\scripts\start_nte_tunnel.ps1"
