@echo off
REM QA PlayTest Manager - double-click to launch (no cd / venv-activate needed)
REM Server: http://127.0.0.1:8765   (PlayTest UI: /playtest/)

cd /d "%~dp0"

REM ── 1. Create venv if it doesn't exist ──────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] 'python' not found. Install Python 3.10+ and add it to PATH.
        pause
        exit /b 1
    )
)

set "PY=.venv\Scripts\python.exe"
set "PIP=.venv\Scripts\pip.exe"

REM ── 2. Install / upgrade all dependencies ───────────────────────────────────
echo [setup] Installing dependencies from requirements.txt...
"%PIP%" install --upgrade pip -q
"%PIP%" install -r requirements.txt -q
if errorlevel 1 (
    echo [ERROR] pip install failed. Check requirements.txt and your network.
    pause
    exit /b 1
)

REM ── 3. Launch control server ─────────────────────────────────────────────────
echo.
echo Launching: %PY% -m manager.control
"%PY%" -m manager.control

echo.
echo [server stopped] press any key to close...
pause >nul
