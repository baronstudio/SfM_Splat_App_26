@echo off
cd /d %~dp0

echo "--- Starting Backend Server ---"
start "Backend" cmd /c ".\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload"

echo "--- Starting Frontend Server ---"
start "Frontend" cmd /c "cd frontend && npm run dev"

@REM echo "--- Opening Application in Browser ---"
@REM timeout /t 5 > nul
@REM start http://localhost:5173
