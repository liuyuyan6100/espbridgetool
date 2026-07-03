@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo =======================================
echo   Serial Bridge - ESP32 Serial Proxy
echo =======================================
echo.

:: 启动前清理占用 8080 端口的旧进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080.*LISTENING" 2^>nul') do (
    echo [INFO] 清理占用 8080 端口的旧进程 (PID: %%a)
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 1 /nobreak >nul
)

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

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] 服务异常退出 (代码: %ERRORLEVEL%)
    pause
)
