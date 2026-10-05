param([string]$SkillHome = '')

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$sourceSkill = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'tangtang-harness'))
if (-not (Test-Path -LiteralPath (Join-Path $sourceSkill 'SKILL.md') -PathType Leaf)) {
    throw "Missing canonical skill: $sourceSkill"
}

$resolvedSkillHome = if ($SkillHome) {
    [IO.Path]::GetFullPath($SkillHome)
} elseif ($env:CODEX_HOME) {
    Join-Path $env:CODEX_HOME 'skills'
} else {
    Join-Path $env:USERPROFILE '.codex\skills'
}
$linkPath = Join-Path $resolvedSkillHome 'QQBot\tangtang-harness'
$existingLink = Get-Item -LiteralPath $linkPath -Force -ErrorAction SilentlyContinue
if ($null -ne $existingLink) {
    if ($existingLink.LinkType -ne 'Junction') {
        throw "Refusing to replace a non-junction path: $linkPath"
    }
    $targets = @($existingLink.Target)
    if ($targets.Count -ne 1 -or -not [string]::Equals(
        [IO.Path]::GetFullPath([string]$targets[0]), $sourceSkill,
        [StringComparison]::OrdinalIgnoreCase)) {
        throw "Existing junction points elsewhere: $linkPath"
    }
    Write-Output "Link already present: $linkPath -> $sourceSkill"
    return
}

New-Item -ItemType Directory -Path (Split-Path -Parent $linkPath) -Force | Out-Null
New-Item -ItemType Junction -Path $linkPath -Target $sourceSkill | Out-Null
Write-Output "Linked: $linkPath -> $sourceSkill"
