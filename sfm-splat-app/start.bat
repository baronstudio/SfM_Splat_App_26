@echo off
cd /d %~dp0

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

echo "--- Starting Backend Server ---"
start "Backend" cmd /c ".\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload"

echo "--- Starting Frontend Server ---"
start "Frontend" cmd /c "cd frontend && npm run dev"

@REM echo "--- Opening Application in Browser ---"
@REM timeout /t 5 > nul
@REM start http://localhost:5173
