@echo off
cd /d "%~dp0"

echo === Policy app: initial setup (Windows) ===
echo Folder: %cd%
echo.

where py >nul 2>&1
if %errorlevel%==0 goto HAVE_PY
where python >nul 2>&1
if %errorlevel%==0 goto HAVE_PYTHON

echo [ERROR] Python not found in PATH.
echo Install Python and check "Add Python to PATH", then re-open CMD.
pause
exit /b 1

:HAVE_PY
set "PYEXE=py -3"
goto DO_SETUP

:HAVE_PYTHON
set "PYEXE=python"
goto DO_SETUP

:DO_SETUP
echo Using: %PYEXE%
%PYEXE% --version
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Creating venv...
  %PYEXE% -m venv .venv
  if not %errorlevel%==0 goto FAIL
)

echo Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip
if not %errorlevel%==0 goto FAIL

echo Installing requirements...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not %errorlevel%==0 goto FAIL

echo.
echo OK. Next: run_windows.bat
pause
exit /b 0

:FAIL
echo.
echo [ERROR] Setup failed. Scroll up for the real error message.
pause
exit /b 1
