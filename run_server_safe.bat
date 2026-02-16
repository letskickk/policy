@echo off
setlocal

cd /d "c:\policy"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv\Scripts\python.exe not found.
  echo Run setup first:
  echo   c:\policy\setup_windows.bat
  pause
  exit /b 1
)

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8000" ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>&1
)

echo URL: http://127.0.0.1:8000
echo Press Ctrl+C to stop.
echo.

".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

echo.
echo (Server exited)
pause
