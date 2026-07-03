@echo off
cd /d "%~dp0"

echo =======================================
echo   Serial Bridge - ESP32 Serial Proxy
echo =======================================
echo.

:: Kill old process occupying port 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING" 2^>nul') do (
    echo [INFO] Killing old process on port 8080, PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

:: Use .venv if available, fall back to venv, then system python
set PYTHON=python
if exist ".venv\Scripts\python.exe" set PYTHON=.venv\Scripts\python.exe
if exist "venv\Scripts\python.exe" if not exist ".venv\Scripts\python.exe" set PYTHON=venv\Scripts\python.exe

:: Check .env
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] .env not found, copying from .env.example ...
        copy .env.example .env >nul
        echo Please edit .env to configure serial port, then restart.
    )
)

echo Config: .env
echo Web UI: http://127.0.0.1:8080
echo.

%PYTHON% serial_bridge.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Server exited with code: %ERRORLEVEL%
    pause
)
