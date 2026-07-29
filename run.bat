@echo off
setlocal
cd /d "%~dp0"

if not exist ".env" (
  echo No .env found. Creating one from .env.example ...
  copy ".env.example" ".env" >nul
  echo Open .env and fill KEEPA_API_KEY + Discord webhooks, or use run_ui.bat instead.
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python not found on PATH.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv venv
)

echo Installing / updating packages...
"venv\Scripts\python.exe" -m pip install --upgrade pip
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: pip install failed.
  pause
  exit /b 1
)

echo Starting monitor (no UI)...
"venv\Scripts\python.exe" main.py %*
pause
