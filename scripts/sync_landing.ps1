<#
.SYNOPSIS
    Upload the local landing zone (SMARD + weather) to the UC volume and verify it.

.DESCRIPTION
    For each source (default: smard, weather) copies data/landing/<source> recursively to
    dbfs:/Volumes/<Catalog>/<Schema>/landing/<source>. Every databricks call is checked via
    $LASTEXITCODE; the script stops with a clear error on the first failure. At the end each
    target is listed with `databricks fs ls` and the script fails if any target is empty.

    The landing volume must exist (run extract_smard_job or 02_extract_weather once, or
    CREATE VOLUME workspace.<schema>.landing).

.EXAMPLE
    .\scripts\sync_landing.ps1
    .\scripts\sync_landing.ps1 -Schema energy_staging -Sources weather
    .\scripts\sync_landing.ps1 -Overwrite   # also replace files that already exist in the volume
#>
[CmdletBinding()]
param(
    [string]$Catalog = "workspace",
    [string]$Schema = "energy_dev",
    [string]$Profile = "free",
    [string]$LocalDir = "data/landing",
    [ValidateSet("smard", "weather")]
    [string[]]$Sources = @("smard", "weather"),
    # Replace existing volume files. Needed to push re-downloaded SMARD refresh chunks; note that
    # bronze re-ingests every overwritten file (cloudFiles.allowOverwrites).
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"

function Invoke-Databricks {
    # Runs the databricks CLI, returns its stdout lines, throws if the exit code is non-zero.
    param(
        [Parameter(Mandatory)] [string]$Step,
        [Parameter(Mandatory)] [string[]]$CliArgs
    )
    $output = & databricks @CliArgs -p $Profile
    if ($LASTEXITCODE -ne 0) {
        throw "FAILED: $Step (databricks $($CliArgs -join ' ') -p $Profile exited with $LASTEXITCODE)"
    }
    return $output
}

if (-not (Get-Command databricks -ErrorAction SilentlyContinue)) {
    throw "FAILED: databricks CLI not found on PATH."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$localBase = if ([System.IO.Path]::IsPathRooted($LocalDir)) { $LocalDir } else { Join-Path $repoRoot $LocalDir }
$volumeBase = "dbfs:/Volumes/$Catalog/$Schema/landing"

# Check every local source before uploading anything, so a missing folder is not silently skipped.
foreach ($source in $Sources) {
    $localPath = Join-Path $localBase $source
    if (-not (Test-Path $localPath -PathType Container)) {
        throw "FAILED: local folder $localPath not found. Run: uv run python -m energy_lakehouse.extract_local --source $source"
    }
    $count = @(Get-ChildItem $localPath -Recurse -File -Filter *.json).Count
    if ($count -eq 0) {
        throw "FAILED: local folder $localPath contains no .json files."
    }
    Write-Host "local  $source : $count files in $localPath"
}

Invoke-Databricks -Step "check landing volume $volumeBase exists" -CliArgs @("fs", "ls", $volumeBase) | Out-Null

foreach ($source in $Sources) {
    $localPath = Join-Path $localBase $source
    $target = "$volumeBase/$source"
    Invoke-Databricks -Step "create $target" -CliArgs @("fs", "mkdir", $target) | Out-Null

    $cpArgs = @("fs", "cp", "-r", $localPath, $target)
    if ($Overwrite) { $cpArgs += "--overwrite" }
    Write-Host "upload $source : $localPath -> $target"
    Invoke-Databricks -Step "upload $source" -CliArgs $cpArgs | Out-Null
}

# Verify each target: listing must succeed and return at least one entry.
foreach ($source in $Sources) {
    $target = "$volumeBase/$source"
    $entries = @(Invoke-Databricks -Step "list $target" -CliArgs @("fs", "ls", $target) |
        Where-Object { $_ -and $_.Trim() })
    if ($entries.Count -eq 0) {
        throw "FAILED: $target is empty after upload."
    }
    Write-Host "verify $source : $($entries.Count) entries in $target ($($entries -join ', '))"
}

Write-Host "OK: synced $($Sources -join ', ') to $volumeBase"
