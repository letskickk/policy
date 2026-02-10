@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run setup_windows.bat first.
  pause
  exit /b 1
)

set "HOST=127.0.0.1"
set "PORT=8000"

echo === Policy app: run server (Windows) ===
echo URL: http://%HOST%:%PORT%
echo Press Ctrl+C to stop.
echo.

.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host %HOST% --port %PORT%

echo.
echo (Server exited)
pause
