param(
    [switch]$Apply,
    [switch]$IncludeInstallers,
    [switch]$IncludeHistoricalArchive
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))

$RelativeTargets = @(
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    "__pycache__",
    "build",
    "dist",
    ".agents",
    "tmp\ui-redesign",
    ".codex\runtime",
    ".codex\profile-ai-disabled-preview",
    "data\manager-state.json",
    "data\smoke_game_api.db",
    "data\generated-smoke",
    "data\today_wife_web_tunnel_url.txt",
    "data\staging",
    "data\asoul_debug",
    "data\asoul_bili_login_qrcode.png",
    "data\nte_custom_preview",
    "data\nte_help_cache_test",
    "data\nte_reference_preview",
    "data\nte_render_dark_test",
    "data\nte_render_test",
    "data\nte_style_preview"
)

$dataRoot = Join-Path $Root "data"
if (Test-Path -LiteralPath $dataRoot) {
    $RelativeTargets += @(
        Get-ChildItem -LiteralPath $dataRoot -Directory -Force |
            Where-Object { $_.Name.Replace(([char]0x200B).ToString(), "") -eq "nte_style_preview" } |
            ForEach-Object { "data\$($_.Name)" }
    )
}

foreach ($eggInfo in Get-ChildItem -LiteralPath $Root -Directory -Filter "*.egg-info" -Force) {
    $RelativeTargets += $eggInfo.Name
}

foreach ($searchRoot in @("bot", "scripts", "tests")) {
    $absoluteSearchRoot = Join-Path $Root $searchRoot
    if (Test-Path -LiteralPath $absoluteSearchRoot) {
        $RelativeTargets += @(
            Get-ChildItem -LiteralPath $absoluteSearchRoot -Directory -Recurse -Force |
                Where-Object { $_.Name -eq "__pycache__" } |
                ForEach-Object { $_.FullName.Substring($Root.TrimEnd("\").Length).TrimStart("\") }
        )
    }
}

if ($IncludeInstallers) {
    $RelativeTargets += "downloads"
}

if ($IncludeHistoricalArchive) {
    $RelativeTargets += "data\local_archive"
}

$seenTargets = New-Object "Collections.Generic.HashSet[string]" ([StringComparer]::OrdinalIgnoreCase)
$targets = @(
    foreach ($relativeTarget in $RelativeTargets) {
        if (-not $seenTargets.Add($relativeTarget)) { continue }
        $candidate = [IO.Path]::GetFullPath((Join-Path $Root $relativeTarget))
        $prefix = $Root.TrimEnd("\") + "\"
        if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Cleanup target escapes project root: $candidate"
        }
        if ($candidate -eq $Root) {
            throw "Cleanup target cannot be the project root."
        }
        if (Test-Path -LiteralPath $candidate) {
            $candidate
        }
    }
)

if ($targets.Count -eq 0) {
    Write-Output "No generated cleanup targets found."
    exit 0
}

foreach ($target in $targets) {
    $relative = $target.Substring($Root.TrimEnd("\").Length).TrimStart("\")
    if ($Apply) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Output "Removed: $relative"
    } else {
        Write-Output "Would remove: $relative"
    }
}

if (-not $Apply) {
    Write-Output "Dry run only. Re-run with -Apply to remove these generated files."
}
