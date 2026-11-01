@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -c "import sys;sys.exit(sys.version_info[:2] < (3,11))" >nul 2>nul
  if not errorlevel 1 (
    .venv\Scripts\python.exe launch.py %*
    goto done
  )
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys;sys.exit(sys.version_info[:2] < (3,11))" >nul 2>nul
  if not errorlevel 1 (
    python launch.py %*
    goto done
  )
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys;sys.exit(sys.version_info[:2] < (3,11))" >nul 2>nul
  if not errorlevel 1 (
    py -3 launch.py %*
    goto done
  )
)
echo Python 3.11+ is needed for the local server. Opening the offline website instead.
echo For the model workbench, install Python from https://www.python.org/downloads/
if "%~1"=="--no-browser" exit /b 2
if "%~3"=="--no-browser" exit /b 2
start "" "%~dp0index.html"
exit /b 0
:done
if errorlevel 1 pause
