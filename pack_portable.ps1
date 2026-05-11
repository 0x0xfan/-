param(
    [switch]$IncludeSecrets,
    [string]$OutputDir = "portable_dist"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stage = Join-Path $OutputDir "infoflow-video-generator_$timestamp"
$zip = "$stage.zip"

if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Path $stage | Out-Null

$excludeDirs = @(
    ".venv", ".venv-win", ".venv-infoflow", ".pytest_cache",
    "runs", "debug_frames", "tmp_gap_card_checks", "tts_test", "volc_batch_test",
    "portable_dist"
)
$excludeFiles = @(
    "*.pyc", "*.log", "*.zip", "*_TEMP_MPY_*.mp4"
)
if (-not $IncludeSecrets) {
    $excludeFiles += "tts.config.yaml"
}

function Test-IsExcludedDir($RelativePath) {
    foreach ($dir in $excludeDirs) {
        if ($RelativePath -eq $dir -or $RelativePath.StartsWith("$dir\")) {
            return $true
        }
    }
    return $false
}

Get-ChildItem -Force | ForEach-Object {
    $relative = $_.Name
    if ($_.PSIsContainer) {
        if (Test-IsExcludedDir $relative) { return }
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stage $relative) -Recurse -Force
    } else {
        foreach ($pattern in $excludeFiles) {
            if ($_.Name -like $pattern) { return }
        }
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stage $relative) -Force
    }
}

if (-not $IncludeSecrets -and -not (Test-Path -LiteralPath (Join-Path $stage "tts.config.yaml"))) {
    Copy-Item -LiteralPath "codex-skills\infoflow-video-generator\tts.config.example.yaml" -Destination (Join-Path $stage "tts.config.yaml")
}

if (Test-Path -LiteralPath $zip) {
    Remove-Item -LiteralPath $zip -Force
}
Compress-Archive -Path $stage -DestinationPath $zip -Force

Write-Host "Portable package created: $zip"
if (-not $IncludeSecrets) {
    Write-Host "Note: real tts.config.yaml was not included. The package contains an empty template."
}
