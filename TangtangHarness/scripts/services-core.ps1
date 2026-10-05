# Harness operations for the existing external Core. No upstream or old config is rewritten.

function Get-HarnessCoreSettings {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $rootPath = [IO.Path]::GetFullPath($HarnessRoot)
    $repositoryRoot = Split-Path -Parent $rootPath
    $paths = @{
        core_dir = Join-Path $repositoryRoot 'GsUID.Core'
        core_python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
        core_runner = Join-Path $repositoryRoot 'scripts\run_gsuid_core.py'
    }
    $coreUrl = 'ws://127.0.0.1:8765'
    $settingsPath = Join-Path $rootPath 'config\settings.json'
    if (Test-Path -LiteralPath $settingsPath) {
        $config = Get-Content -LiteralPath $settingsPath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($config.PSObject.Properties.Name -contains 'extra' -and $null -ne $config.extra) {
            if ($config.extra.PSObject.Properties.Name -contains 'core' -and $null -ne $config.extra.core -and
                $config.extra.core.PSObject.Properties.Name -contains 'url' -and $config.extra.core.url) {
                $coreUrl = [string]$config.extra.core.url
            }
            if ($config.extra.PSObject.Properties.Name -contains 'operations' -and $null -ne $config.extra.operations) {
                foreach ($key in @('core_dir', 'core_python', 'core_runner')) {
                    if ($config.extra.operations.PSObject.Properties.Name -contains $key -and $config.extra.operations.$key) {
                        $value = [string]$config.extra.operations.$key
                        if ([IO.Path]::IsPathRooted($value)) { $paths[$key] = $value }
                        else { $paths[$key] = Join-Path $rootPath $value }
                    }
                }
            }
        }
    }
    $endpoint = [Uri]$coreUrl
    $scheme = 'http'
    if ($endpoint.Scheme -eq 'wss' -or $endpoint.Scheme -eq 'https') { $scheme = 'https' }
    return [pscustomobject]@{
        root = $rootPath
        directory = [IO.Path]::GetFullPath($paths.core_dir)
        python = [IO.Path]::GetFullPath($paths.core_python)
        runner = [IO.Path]::GetFullPath($paths.core_runner)
        entry = Join-Path ([IO.Path]::GetFullPath($paths.core_dir)) 'gsuid_core\core.py'
        port = $endpoint.Port
        url = '{0}://{1}:{2}/' -f $scheme, $endpoint.DnsSafeHost, $endpoint.Port
        state_path = Join-Path $rootPath 'data\core-process.json'
    }
}

function Test-HarnessCoreProcess {
    param([Parameter(Mandatory = $true)][object]$Process, [Parameter(Mandatory = $true)][object]$Settings)

    if ($Process.Name -notlike 'python*.exe' -or -not $Process.CommandLine) { return $false }
    foreach ($entry in @($Settings.runner, $Settings.entry)) {
        $pattern = [regex]::Escape($entry).Replace('\\', '[\\/]')
        if ([string]$Process.CommandLine -match ('(?i)(?:^|\s|")' + $pattern + '(?:\s|"|$)')) { return $true }
    }
    return $false
}

function Get-HarnessCoreProcesses {
    param([Parameter(Mandatory = $true)][object]$Settings)

    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $owned = @($snapshot | Where-Object { Test-HarnessCoreProcess -Process $_ -Settings $Settings })
    $ids = @($owned | ForEach-Object { [int]$_.ProcessId })
    do {
        $children = @($snapshot | Where-Object { [int]$_.ParentProcessId -in $ids -and [int]$_.ProcessId -notin $ids })
        $owned += $children
        $ids += @($children | ForEach-Object { [int]$_.ProcessId })
    } while ($children.Count -gt 0)
    return $owned
}

function Get-HarnessCoreStatus {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $settings = Get-HarnessCoreSettings -HarnessRoot $HarnessRoot
    $processes = @(Get-HarnessCoreProcesses -Settings $settings)
    $processIds = @($processes | ForEach-Object { [int]$_.ProcessId })
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $settings.port -ErrorAction SilentlyContinue)
    $ready = @($listeners | Where-Object { [int]$_.OwningProcess -in $processIds }).Count -gt 0
    $installed = (Test-Path -LiteralPath $settings.python) -and (Test-Path -LiteralPath $settings.runner) -and
        (Test-Path -LiteralPath $settings.entry)
    $state = 'stopped'
    $detail = 'Core is stopped.'
    if (-not $installed) { $state = 'not_installed'; $detail = 'Core entry, runner or Python is missing.' }
    if ($processIds.Count -gt 0) {
        $state = 'running'
        $detail = 'Core process is running; owned listener ready={0}.' -f $ready
    }
    elseif ($listeners.Count -gt 0) {
        $state = 'port_in_use'
        $detail = 'Core port {0} is occupied by an unrelated process.' -f $settings.port
    }
    return [pscustomobject]@{
        state = $state
        pids = $processIds
        ready = $ready
        url = $settings.url
        detail = $detail
    }
}

function Start-HarnessCore {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $settings = Get-HarnessCoreSettings -HarnessRoot $HarnessRoot
    $status = Get-HarnessCoreStatus -HarnessRoot $HarnessRoot
    if ($status.state -eq 'running' -and $status.ready) { return $status }
    if ($status.state -eq 'port_in_use') { throw $status.detail }
    if ($status.state -eq 'not_installed') { throw "Core runtime is incomplete: $($settings.directory)" }
    if ($status.state -ne 'running') {
        $logDir = Join-Path $settings.root 'logs'
        New-Item -ItemType Directory -Force -Path $logDir, (Split-Path -Parent $settings.state_path) | Out-Null
        $options = @{
            FilePath = $settings.python
            ArgumentList = '-u "{0}"' -f $settings.runner
            WorkingDirectory = $settings.directory
            WindowStyle = 'Hidden'
            PassThru = $true
            RedirectStandardOutput = Join-Path $logDir 'core.out.log'
            RedirectStandardError = Join-Path $logDir 'core.err.log'
            ErrorAction = 'Stop'
        }
        $previousCoreDirectory = [Environment]::GetEnvironmentVariable('GSUID_CORE_DIR', 'Process')
        try {
            $env:GSUID_CORE_DIR = $settings.directory
            $process = Start-Process @options
        }
        finally { [Environment]::SetEnvironmentVariable('GSUID_CORE_DIR', $previousCoreDirectory, 'Process') }
        [pscustomobject]@{
            pid = $process.Id
            runner = $settings.runner
            core_dir = $settings.directory
            started_at = [DateTime]::UtcNow.ToString('o')
        } | ConvertTo-Json | Set-Content -LiteralPath $settings.state_path -Encoding utf8
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $status = Get-HarnessCoreStatus -HarnessRoot $HarnessRoot
        if ($status.state -eq 'running' -and $status.ready) { return $status }
        if ($status.state -ne 'running') {
            throw 'Core exited during startup. Review Harness logs\core.err.log and core.out.log.'
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Core is running but did not listen within 30 seconds. Review Harness logs\core.err.log and core.out.log.'
}

function Stop-HarnessCore {
    param([Parameter(Mandatory = $true)][string]$HarnessRoot)

    $settings = Get-HarnessCoreSettings -HarnessRoot $HarnessRoot
    $processes = @(Get-HarnessCoreProcesses -Settings $settings)
    $processIds = @($processes | ForEach-Object { [int]$_.ProcessId })
    $byId = @{}
    foreach ($process in $processes) { $byId[[int]$process.ProcessId] = $process }
    $ordered = @($processes | Sort-Object -Descending -Property @{
        Expression = {
            $depth = 0
            $parentId = [int]$_.ParentProcessId
            while ($parentId -in $processIds) {
                $depth += 1
                $parentId = [int]$byId[$parentId].ParentProcessId
            }
            $depth
        }
    })
    foreach ($process in $ordered) {
        $processId = [int]$process.ProcessId
        if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { continue }
        Stop-Process -Id $processId -Force -ErrorAction Stop
        Wait-Process -Id $processId -Timeout 15 -ErrorAction SilentlyContinue
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) { throw "Core PID $processId did not stop." }
    }
    Remove-Item -LiteralPath $settings.state_path -Force -ErrorAction SilentlyContinue
    return Get-HarnessCoreStatus -HarnessRoot $HarnessRoot
}
