@echo off
REM Run YellowLink straight from the source code (needs Python 3.11+ from python.org).
REM First run installs dependencies into a local .venv folder; later runs start instantly.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Setting up YellowLink for the first time...
    py -3 -m venv .venv || python -m venv .venv || (echo Python not found. Install it from https://www.python.org/downloads/ & pause & exit /b 1)
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt || (pause & exit /b 1)
)
.venv\Scripts\python.exe -m yellowlink %*
if errorlevel 1 pause
