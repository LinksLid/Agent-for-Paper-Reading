$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Force -Path '.cache\tmp' | Out-Null
$env:TEMP = Join-Path $projectRoot '.cache\tmp'
$env:TMP = $env:TEMP
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment.' }
}
if (Test-Path -LiteralPath 'requirements-lock.txt') {
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
} else {
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies.' }
Write-Output 'Setup complete. Run scripts\start.ps1 to open PaperTeam.'
