@echo off
cd /d "%~dp0"

echo =======================================
echo   Serial Bridge - ESP32 Serial Proxy
echo =======================================
echo.

:: Use .venv if available, fall back to venv, then system python
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else if exist "venv\Scripts\python.exe" (
    set PYTHON=venv\Scripts\python.exe
) else (
    set PYTHON=python
)

:: Check .env
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] .env not found, copying from .env.example ...
        copy .env.example .env >nul
        echo Please edit .env to configure serial port, then restart.
        echo.
    )
)

echo Config: .env
echo Web UI: http://127.0.0.1:8080
echo.

%PYTHON% serial_bridge.py

pause