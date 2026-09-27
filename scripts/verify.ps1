param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$webRoot = Join-Path $projectRoot "apps\web"
$webBin = Join-Path $webRoot "node_modules\.bin"
$pytestRunId = [guid]::NewGuid().ToString("N")
$pytestTemp = Join-Path $projectRoot ".verify-tmp\pytest-$pytestRunId"

function Invoke-FrontendCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$DisplayName,
        [Parameter(Mandatory = $true)]
        [string[]]$LocalArguments,
        [Parameter(Mandatory = $true)]
        [string[]]$PnpmArguments
    )

    $localCommand = Join-Path $webBin "$Name.cmd"
    if (Test-Path -LiteralPath $localCommand) {
        Push-Location $webRoot
        try {
            & $localCommand @LocalArguments
            $exitCode = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
    }
    else {
        & pnpm --dir apps/web @PnpmArguments
        $exitCode = $LASTEXITCODE
    }

    if ($exitCode -ne 0) {
        throw "$DisplayName failed"
    }
}

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
        Invoke-FrontendCommand `
            -Name "vue-tsc" `
            -DisplayName "Frontend typecheck" `
            -LocalArguments @("-b") `
            -PnpmArguments @("typecheck")

        Write-Host "==> Frontend tests"
        Invoke-FrontendCommand `
            -Name "vitest" `
            -DisplayName "Frontend tests" `
            -LocalArguments @("run") `
            -PnpmArguments @("test")

        Write-Host "==> Frontend build"
        Invoke-FrontendCommand `
            -Name "vite" `
            -DisplayName "Frontend build" `
            -LocalArguments @("build") `
            -PnpmArguments @("build")
    }
}
finally {
    if (Test-Path -LiteralPath $pytestTemp) {
        Remove-Item -LiteralPath $pytestTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}
