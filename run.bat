@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_CMD="

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=.venv\Scripts\python.exe"
)

if not defined PYTHON_CMD (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=py"
    )
)

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
        "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PYTHON_CMD=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    )
)

if not defined PYTHON_CMD (
    echo Could not find a working Python install.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo Then run this file again.
    pause
    exit /b 1
)

"%PYTHON_CMD%" -c "import PySide6, requests, bs4, lxml" >nul 2>nul
if errorlevel 1 (
    echo Missing Python dependencies.
    echo Installing dependencies from requirements.txt...
    "%PYTHON_CMD%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed.
        pause
        exit /b 1
    )
)

"%PYTHON_CMD%" run.py
if errorlevel 1 (
    echo.
    echo DOS Scraper exited with an error.
    pause
    exit /b 1
)

endlocal
