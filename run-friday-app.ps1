param()

$ErrorActionPreference = "Stop"
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktopDir = Join-Path $repoRoot 'desktop-app'
Set-Location $desktopDir

if (-not (Test-Path (Join-Path $desktopDir 'node_modules'))) {
    npm install
}

npm start
