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

:: Auto-detect ESP32 serial port if SERIAL_PORT not set or port unavailable
:: This helps when USB re-enumeration changes COM port number
%PYTHON% -c "import serial.tools.list_ports as lp; ports=[p for p in lp.comports() if 'USB' in (p.hwid or '') or 'CH340' in (p.description or '') or 'CP210' in (p.description or '') or 'JTAG' in (p.description or '')]; print(ports[0].device if ports else '', end='')" > "%TEMP%\esp_port.txt" 2>nul
set /p DETECTED_PORT= < "%TEMP%\esp_port.txt"
del "%TEMP%\esp_port.txt" 2>nul

if defined DETECTED_PORT (
    if not "%DETECTED_PORT%"=="" (
        echo [INFO] Detected ESP32 serial port: %DETECTED_PORT%
    )
)

echo Config: .env
echo Web UI: http://127.0.0.1:8080
echo MCP:    Configure mcp_server.py in your agent (TRAE/Cursor/Claude)
echo.
echo =======================================
echo  Auto-recovery enabled:
echo  - USB hot-plug detection
echo  - Dynamic port tracking (COM6 ^<-^> COM5)
echo  - Flash retry with 8 attempts
echo =======================================
echo.

%PYTHON% serial_bridge.py

echo.
echo [INFO] Server stopped (exit code: %ERRORLEVEL%).
echo.
echo Press any key to close...
pause >nul
