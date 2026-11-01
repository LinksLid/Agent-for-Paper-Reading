$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:PYTHONIOENCODING='utf-8'
$taskPython=Join-Path $taskRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $taskPython) { & $taskPython launch.py }
elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3 launch.py }
else { & python launch.py }
