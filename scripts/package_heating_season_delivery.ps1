param(
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workspaceRoot = (Resolve-Path (Join-Path $projectRoot "..")).Path
$stamp = Get-Date -Format "yyyyMMdd"
$packageName = "north_heat_challenge_heating_season_code_$stamp"
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $workspaceRoot "packages"
}
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Path $destinationPath -Force | Out-Null

$stagingBase = Join-Path $projectRoot "tmp\delivery_packages"
$stagingBaseFull = [System.IO.Path]::GetFullPath($stagingBase)
if (-not $stagingBaseFull.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe staging path: $stagingBaseFull"
}
New-Item -ItemType Directory -Path $stagingBaseFull -Force | Out-Null
$stagingRoot = Join-Path $stagingBaseFull $packageName
if (Test-Path -LiteralPath $stagingRoot) {
    $resolvedStaging = [System.IO.Path]::GetFullPath($stagingRoot)
    if (-not $resolvedStaging.StartsWith($stagingBaseFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside staging base: $resolvedStaging"
    }
    Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null

foreach ($directory in @("src", "scripts", "tests", "data", "docs")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $directory) -Destination $stagingRoot -Recurse
}
foreach ($file in @("README.md", "requirements.txt", "pytest.ini", "HEATING_SEASON_DELIVERY.md")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $stagingRoot
}

Get-ChildItem -LiteralPath $stagingRoot -Directory -Recurse -Force |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache") } |
    Sort-Object FullName -Descending |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $stagingRoot -File -Recurse -Force |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") -or $_.Name -like '~$*' } |
    Remove-Item -Force

$representativeTarget = Join-Path $stagingRoot "evidence\representative_days"
New-Item -ItemType Directory -Path $representativeTarget -Force | Out-Null
foreach ($scenario in @("scenario1_urumqi", "scenario2_guyuan", "scenario3_wuhan")) {
    $source = Join-Path $projectRoot "outputs\v11_4_representative_days\$scenario\representative_days_calibrated.csv"
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing calibrated representative-day input: $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $representativeTarget "$scenario.csv")
}

$annualRoot = Join-Path $projectRoot "outputs\v11_5_three_scenario_annual_replay"
$annualTarget = Join-Path $stagingRoot "evidence\annual_8760"
New-Item -ItemType Directory -Path $annualTarget -Force | Out-Null
foreach ($file in @("annual_replay_batch_summary.csv", "annual_replay_batch_summary.json")) {
    Copy-Item -LiteralPath (Join-Path $annualRoot $file) -Destination $annualTarget
}
$evidenceNames = @(
    "annual_replay_acceptance_certificate.json",
    "annual_replay_kpi_comparison.csv",
    "annual_replay_preflight_summary.json",
    "annual_replay_summary.json",
    "chronology_quality_audit.json",
    "fixed_investment_config.yaml",
    "fixed_investment_readiness_audit.json",
    "investment_freeze_audit.csv",
    "model_execution_profile.json",
    "scenario_scope_audit.json",
    "seasonal_storage_postcheck.csv"
)
foreach ($scenario in @("scenario1_urumqi", "scenario2_guyuan", "scenario3_wuhan")) {
    $scenarioSource = Join-Path $annualRoot $scenario
    $scenarioTarget = Join-Path $annualTarget $scenario
    New-Item -ItemType Directory -Path $scenarioTarget -Force | Out-Null
    foreach ($name in $evidenceNames) {
        $source = Join-Path $scenarioSource $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $scenarioTarget
        }
    }
    foreach ($subdir in @("representative_planning", "fixed_investment_annual_replay")) {
        $summary = Join-Path $scenarioSource "$subdir\summary.json"
        if (Test-Path -LiteralPath $summary) {
            $targetSubdir = Join-Path $scenarioTarget $subdir
            New-Item -ItemType Directory -Path $targetSubdir -Force | Out-Null
            Copy-Item -LiteralPath $summary -Destination $targetSubdir
        }
    }
}

$figureSource = Join-Path $projectRoot "outputs\v11_5_figures_hd"
$figureTarget = Join-Path $stagingRoot "evidence\figures"
New-Item -ItemType Directory -Path $figureTarget -Force | Out-Null
Get-ChildItem -LiteralPath $figureSource -File |
    Where-Object {
        $_.Name -match '^fig0[1-3]_' -or
        $_.Name -match '^fig04_2024_2025_heating_season_scope\.'
    } |
    Copy-Item -Destination $figureTarget

$manifestPath = Join-Path $stagingRoot "PACKAGE_MANIFEST.csv"
$manifestRows = Get-ChildItem -LiteralPath $stagingRoot -File -Recurse |
    Where-Object { $_.FullName -ne $manifestPath } |
    Sort-Object FullName |
    ForEach-Object {
        [pscustomobject]@{
            path = $_.FullName.Substring($stagingRoot.Length + 1).Replace('\', '/')
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
$manifestRows | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding utf8

$zipPath = Join-Path $destinationPath "$packageName.zip"
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $stagingRoot -DestinationPath $zipPath -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$hashPath = "$zipPath.sha256.txt"
Set-Content -LiteralPath $hashPath -Value "$zipHash  $([System.IO.Path]::GetFileName($zipPath))" -Encoding utf8

[pscustomobject]@{
    zip = $zipPath
    sha256 = $zipHash
    files = (Get-ChildItem -LiteralPath $stagingRoot -File -Recurse).Count
    size_mb = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 2)
    hash_file = $hashPath
}
