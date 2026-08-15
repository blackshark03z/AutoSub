@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo AutoSub could not find Python on this computer.
  echo Install the prepared AutoSub runtime, then double-click this file again.
  pause
  exit /b 1
)

python tools\local_launcher.py --project-root "%CD%"
if errorlevel 1 (
  echo.
  echo AutoSub did not start. See the diagnostic above, then press any key to close this window.
  pause
)
