param(
    [string]$CodexHome = "$HOME\.codex",
    [switch]$SkipSync
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$skillsRoot = Join-Path $CodexHome "skills"
New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null

$destination = Join-Path $skillsRoot "stock-pilot"
Copy-Item -Recurse -Force (Join-Path $repo "skill\stock-pilot") $destination
foreach ($name in @("longbridge-content", "longbridge-fundamentals")) {
    $source = Join-Path $repo "dependencies\skills\$name"
    if (Test-Path $source) {
        Copy-Item -Recurse -Force $source (Join-Path $skillsRoot $name)
    }
}

if (-not $SkipSync) {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv is required. Install it from https://docs.astral.sh/uv/"
    }
    uv sync --frozen --project $destination
}

$required = @(
    "technical-analysis\scripts\technicals.py",
    "api-data-fetcher\scripts\fetch_market_macro.py"
)
$missing = @($required | Where-Object { -not (Test-Path (Join-Path $skillsRoot $_)) })

Write-Host "Installed stock-pilot at $destination"
if ($missing.Count -gt 0) {
    Write-Warning "Live collect/run still requires: $($missing -join ', ')"
    Write-Host "See DEPENDENCIES.md, install authorized copies, then run doctor."
}
