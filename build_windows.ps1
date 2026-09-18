[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$distPath = Join-Path $projectRoot "dist\InstagramFollowTracker"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Virtual environment not found. Run: python -m venv .venv"
}

Push-Location $projectRoot
try {
    & $pythonPath -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install build dependencies."
    }

    $previousBrowserPath = $env:PLAYWRIGHT_BROWSERS_PATH
    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    try {
        & $pythonPath -m playwright install chromium --no-shell
        if ($LASTEXITCODE -ne 0) {
            throw "Could not install the bundled Chromium browser."
        }

        & $pythonPath -m PyInstaller `
            --noconfirm `
            --clean `
            --onedir `
            --windowed `
            --name InstagramFollowTracker `
            app.py
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller build failed."
        }
    }
    finally {
        $env:PLAYWRIGHT_BROWSERS_PATH = $previousBrowserPath
    }

    Copy-Item -LiteralPath "README.md" -Destination $distPath -Force
    New-Item -ItemType Directory -Path (Join-Path $distPath "docs") -Force | Out-Null
    Copy-Item -LiteralPath "docs\gui.png" -Destination (Join-Path $distPath "docs\gui.png") -Force

    Write-Host ""
    Write-Host "Build completed:"
    Write-Host (Join-Path $distPath "InstagramFollowTracker.exe")
}
finally {
    Pop-Location
}
