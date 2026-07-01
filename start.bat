@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo =======================================
echo   Serial Bridge — ESP32 串口代理服务
echo =======================================
echo.

:: 使用内置 venv（如果有）
if exist "venv\Scripts\python.exe" (
    set PYTHON=venv\Scripts\python.exe
) else (
    set PYTHON=python
)

:: 默认参数
set PORT=COM6
set BAUDRATE=115200
set HTTP_PORT=8080
set PROJECT_DIR=

:: 检查命令行参数
if not "%1"=="" set PORT=%1
if not "%2"=="" set BAUDRATE=%2

echo  串口: %PORT% @ %BAUDRATE%
echo  HTTP: http://127.0.0.1:%HTTP_PORT%
echo.

%PYTHON% serial_bridge.py --port %PORT% --baud %BAUDRATE% --port-http %HTTP_PORT% %PROJECT_DIR%

pause