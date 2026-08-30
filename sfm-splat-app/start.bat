@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

@REM ---------------------------------------------------------------------
@REM Where this instance listens.
@REM
@REM   BIND_HOST   0.0.0.0 exposes the app to the local network (staging box).
@REM               Set it to 127.0.0.1 for a private, workstation-only run.
@REM   API_PORT    uvicorn.   UI_PORT   the Vite dev server.
@REM
@REM Override without editing this file:  start.bat 127.0.0.1
@REM ---------------------------------------------------------------------
set "BIND_HOST=0.0.0.0"
if not "%~1"=="" set "BIND_HOST=%~1"
set "API_PORT=8000"
set "UI_PORT=5173"

@REM Preflight. A missing venv used to open a "Backend" window that printed
@REM "the system cannot find the path specified" and closed before it could be
@REM read, so the only symptom was a server that never came up.
if not exist ".\.venv\Scripts\python.exe" (
    echo [ERROR] No virtual environment at .\.venv\Scripts\python.exe
    echo         Create it:  py -3.12 -m venv .venv
    echo         Then:       .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
if not exist ".\frontend\node_modules" (
    echo [ERROR] No frontend dependencies at .\frontend\node_modules
    echo         Install them:  cd frontend ^&^& npm install
    pause
    exit /b 1
)

@REM The LAN address of this machine, for the banner below. It is the IPv4 of
@REM the interface that actually has a default gateway: a workstation with
@REM VirtualBox or WSL installed answers 192.168.56.1 first to a plain ipconfig,
@REM which is a host-only adapter nobody else on the network can reach - a URL
@REM that looks right and works from exactly one machine.
set "LAN_IP="
for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null } | Select-Object -First 1).IPv4Address.IPAddress" 2^>nul`) do set "LAN_IP=%%a"
@REM Fallback for a machine without that cmdlet: the first IPv4 ipconfig lists.
@REM On a French Windows the line reads "Adresse IPv4", so the match is on the
@REM token that does not get translated.
if not defined LAN_IP (
    for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
        if not defined LAN_IP set "LAN_IP=%%a"
    )
)
set "LAN_IP=%LAN_IP: =%"
if not defined LAN_IP set "LAN_IP=<this machine>"

echo "--- Starting Backend Server ---"
start "Backend" cmd /c ".\.venv\Scripts\python.exe -m uvicorn backend.main:app --host %BIND_HOST% --port %API_PORT% --reload"

echo "--- Starting Frontend Server ---"
@REM --host binds every interface; --strictPort refuses to slide to 5174 on a
@REM clash, because a staging URL handed to somebody else has to keep working.
@REM The page talks to its own origin and Vite proxies /api, /static and /ws to
@REM the backend on the loopback (vite.config.ts), so only UI_PORT has to be
@REM reachable from another machine.
start "Frontend" cmd /c "cd frontend && npm run dev -- --host %BIND_HOST% --port %UI_PORT% --strictPort"

echo.
if "%BIND_HOST%"=="127.0.0.1" (
    echo   Local only:  http://127.0.0.1:%UI_PORT%
) else (
    echo   On this machine:  http://localhost:%UI_PORT%
    echo   On the network:   http://%LAN_IP%:%UI_PORT%
    echo.
    echo   If the network address does not answer, Windows Firewall is holding
    echo   the port. Once, from an elevated prompt:
    echo.
    echo     netsh advfirewall firewall add rule name="SfM Splat App UI" dir=in action=allow protocol=TCP localport=%UI_PORT% profile=private
    echo.
    echo   No authentication and no sandbox: this app runs local binaries and
    echo   reads server-side folders on request. Trusted subnet only, never a
    echo   port forward.
)
echo.

@REM echo "--- Opening Application in Browser ---"
@REM timeout /t 5 > nul
@REM start http://localhost:5173
