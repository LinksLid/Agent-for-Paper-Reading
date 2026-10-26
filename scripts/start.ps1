$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& '.\.venv\Scripts\python.exe' -m streamlit run app.py --server.address 127.0.0.1
