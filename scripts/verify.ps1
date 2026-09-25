param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pytestTemp = Join-Path $projectRoot ".verify-tmp\pytest"

if (-not $BackendOnly -and -not $FrontendOnly) {
    $runBackend = $true
    $runFrontend = $true
}
else {
    $runBackend = $BackendOnly
    $runFrontend = $FrontendOnly
}

Push-Location $projectRoot
try {
    if ($runBackend) {
        if (-not (Test-Path -LiteralPath $python)) {
            throw "Python virtual environment not found at $python"
        }

        Write-Host "==> Backend lint"
        & $python -m ruff check apps/api
        if ($LASTEXITCODE -ne 0) {
            throw "Backend lint failed"
        }

        Write-Host "==> Backend tests"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pytestTemp) | Out-Null
        & $python -m pytest apps/api/tests -q -p no:cacheprovider --basetemp=$pytestTemp
        if ($LASTEXITCODE -ne 0) {
            throw "Backend tests failed"
        }
    }

    if ($runFrontend) {
        Write-Host "==> Frontend typecheck"
        & pnpm --dir apps/web typecheck
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend typecheck failed"
        }

        Write-Host "==> Frontend tests"
        & pnpm --dir apps/web test
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend tests failed"
        }

        Write-Host "==> Frontend build"
        & pnpm --dir apps/web build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend build failed"
        }
    }
}
finally {
    Pop-Location
}
