@echo off
REM QA PlayTest Manager - double-click to launch (no cd / venv-activate needed)
REM Server: http://127.0.0.1:8765   (PlayTest UI: /playtest/)

cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo Launching: %PY% -m manager.control
"%PY%" -m manager.control

echo.
echo [server stopped] press any key to close...
pause >nul
