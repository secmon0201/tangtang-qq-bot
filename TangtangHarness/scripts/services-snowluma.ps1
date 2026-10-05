# SnowLuma lifecycle for Harness. Read the installed configuration without rewriting it.
# QQ login remains manual; stopping this service never stops QQ.exe.

function Get-HarnessSnowLumaSettings {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $rootPath = [IO.Path]::GetFullPath($HarnessRoot)
    $repositoryRoot = Split-Path -Parent $rootPath
    $snowLumaDir = Join-Path $repositoryRoot 'SnowLuma'
    $harnessPort = 8090
    $settingsPath = Join-Path $rootPath 'config\settings.json'
    if (Test-Path -LiteralPath $settingsPath) {
        $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($settings.PSObject.Properties.Name -contains 'port') { $harnessPort = [int]$settings.port }
        if ($settings.PSObject.Properties.Name -contains 'extra' -and $null -ne $settings.extra -and
            $settings.extra.PSObject.Properties.Name -contains 'operations' -and $null -ne $settings.extra.operations -and
            $settings.extra.operations.PSObject.Properties.Name -contains 'snowluma_dir' -and $settings.extra.operations.snowluma_dir) {
            $configuredDir = [string]$settings.extra.operations.snowluma_dir
            if ([IO.Path]::IsPathRooted($configuredDir)) { $snowLumaDir = $configuredDir }
            else { $snowLumaDir = Join-Path $rootPath $configuredDir }
        }
    }
    $snowLumaDir = [IO.Path]::GetFullPath($snowLumaDir)
    $webUiPort = 5099
    $webUiScheme = 'http'
    $runtimePath = Join-Path $snowLumaDir 'config\runtime.json'
    if (Test-Path -LiteralPath $runtimePath) {
        $runtime = Get-Content -LiteralPath $runtimePath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($runtime.PSObject.Properties.Name -contains 'webuiPort' -and $runtime.webuiPort) {
            $webUiPort = [int]$runtime.webuiPort
        }
        if ($runtime.PSObject.Properties.Name -contains 'webuiTls' -and $null -ne $runtime.webuiTls -and
            $runtime.webuiTls.PSObject.Properties.Name -contains 'enabled' -and $runtime.webuiTls.enabled) {
            $webUiScheme = 'https'
        }
    }
    return [pscustomobject]@{
        root = $rootPath
        directory = $snowLumaDir
        node = Join-Path $snowLumaDir 'node.exe'
        entry = Join-Path $snowLumaDir 'index.mjs'
        webui_port = $webUiPort
        webui_url = '{0}://127.0.0.1:{1}/' -f $webUiScheme, $webUiPort
        harness_port = $harnessPort
        state_path = Join-Path $rootPath 'data\snowluma-process.json'
    }
}

function Test-HarnessSnowLumaProcess {
    param([Parameter(Mandatory = $true)][object]$Process, [Parameter(Mandatory = $true)][object]$Settings)

    if (-not $Process.ExecutablePath -or -not $Process.CommandLine) { return $false }
    $absoluteEntry = [regex]::Escape($Settings.entry)
    $hasAbsoluteEntry = [string]$Process.CommandLine -match ('(?i)(?:^|\s|")' + $absoluteEntry + '(?:\s|"|$)')
    $hasRelativeEntry = [string]$Process.CommandLine -match '(?i)(?:^|\s|")(?:\.\\|\./)?index\.mjs(?:\s|"|$)'
    $bundledNode = [IO.Path]::GetFullPath([string]$Process.ExecutablePath) -eq $Settings.node
    return ($hasAbsoluteEntry -or ($bundledNode -and $hasRelativeEntry))
}

function Get-HarnessSnowLumaProcesses {
    param([Parameter(Mandatory = $true)][object]$Settings)

    return @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction Stop | Where-Object {
        Test-HarnessSnowLumaProcess -Process $_ -Settings $Settings
    })
}

function Get-HarnessSnowLumaStatus {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $settings = Get-HarnessSnowLumaSettings -HarnessRoot $HarnessRoot
    $processes = @(Get-HarnessSnowLumaProcesses -Settings $settings)
    $processIds = @($processes | ForEach-Object { [int]$_.ProcessId })
    $installed = (Test-Path -LiteralPath $settings.node) -and (Test-Path -LiteralPath $settings.entry)
    $webUiReady = $false
    $connected = $null
    $state = 'stopped'
    $detail = 'SnowLuma is stopped; QQ login is managed separately.'
    if (-not $installed) { $state = 'not_installed'; $detail = 'SnowLuma node.exe or index.mjs is missing.' }
    if ($processIds.Count -gt 0) {
        $state = 'running'
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $settings.webui_port -ErrorAction SilentlyContinue)
        $webUiReady = @($listeners | Where-Object { [int]$_.OwningProcess -in $processIds }).Count -gt 0
        try {
            $connections = @(Get-NetTCPConnection -State Established -RemotePort $settings.harness_port -ErrorAction Stop)
            $connected = @($connections | Where-Object {
                [int]$_.OwningProcess -in $processIds -and $_.RemoteAddress -in @('127.0.0.1', '::1', '::ffff:127.0.0.1')
            }).Count -gt 0
        }
        catch {
            # An empty TCP result is normal when Harness is stopped. A provider failure remains unknown.
            if ($_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*') { $connected = $false }
        }
        $detail = 'SnowLuma is running; WebUI ready={0}; Harness connection={1}.' -f $webUiReady, $connected
    }
    return [pscustomobject]@{
        state = $state
        pids = $processIds
        webui_url = $settings.webui_url
        webui_ready = $webUiReady
        connected = $connected
        detail = $detail
    }
}

function Start-HarnessSnowLuma {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $status = Get-HarnessSnowLumaStatus -HarnessRoot $HarnessRoot
    if ($status.state -eq 'running') { return $status }
    $settings = Get-HarnessSnowLumaSettings -HarnessRoot $HarnessRoot
    if ($status.state -eq 'not_installed') { throw "SnowLuma runtime is incomplete: $($settings.directory)" }
    $logDir = Join-Path $settings.root 'logs'
    New-Item -ItemType Directory -Force -Path $logDir, (Split-Path -Parent $settings.state_path) | Out-Null
    $startOptions = @{
        FilePath = $settings.node
        ArgumentList = 'index.mjs'
        WorkingDirectory = $settings.directory
        WindowStyle = 'Hidden'
        PassThru = $true
        RedirectStandardOutput = Join-Path $logDir 'snowluma.out.log'
        RedirectStandardError = Join-Path $logDir 'snowluma.err.log'
        ErrorAction = 'Stop'
    }
    $process = Start-Process @startOptions
    [pscustomobject]@{
        pid = $process.Id
        executable = $settings.node
        entry = $settings.entry
        started_at = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $settings.state_path -Encoding utf8
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) {
            Remove-Item -LiteralPath $settings.state_path -Force -ErrorAction SilentlyContinue
            throw "SnowLuma exited with code $($process.ExitCode). Review Harness logs\snowluma.err.log."
        }
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $settings.webui_port -ErrorAction SilentlyContinue)
        if (@($listeners | Where-Object { [int]$_.OwningProcess -eq $process.Id }).Count -gt 0) { break }
    }
    return Get-HarnessSnowLumaStatus -HarnessRoot $HarnessRoot
}

function Stop-HarnessSnowLuma {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $settings = Get-HarnessSnowLumaSettings -HarnessRoot $HarnessRoot
    $processes = @(Get-HarnessSnowLumaProcesses -Settings $settings)
    foreach ($process in $processes) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
        Wait-Process -Id ([int]$process.ProcessId) -Timeout 15 -ErrorAction SilentlyContinue
        if (Get-Process -Id ([int]$process.ProcessId) -ErrorAction SilentlyContinue) {
            throw "SnowLuma PID $($process.ProcessId) did not stop."
        }
    }
    Remove-Item -LiteralPath $settings.state_path -Force -ErrorAction SilentlyContinue
    return Get-HarnessSnowLumaStatus -HarnessRoot $HarnessRoot
}
