param(
    [string]$CodexHome = "$HOME\.codex"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$skillsRoot = Join-Path $CodexHome "skills"
New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null

Copy-Item -Recurse -Force (Join-Path $repo "skill\stock-pilot") (Join-Path $skillsRoot "stock-pilot")
foreach ($name in @("longbridge-content", "longbridge-fundamentals")) {
    $source = Join-Path $repo "dependencies\skills\$name"
    if (Test-Path $source) {
        Copy-Item -Recurse -Force $source (Join-Path $skillsRoot $name)
    }
}

Write-Host "Installed stock-pilot. Install the remaining external dependency skills before running doctor."
