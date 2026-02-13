@echo off
REM ========================================
REM  AI 공약 멘토링 - 서버 실행 (집 노트북용)
REM  - 프로젝트 폴더에서 더블클릭으로 실행
REM  - 기본 포트: 8000  (http://127.0.0.1:8000)
REM ========================================
REM 창이 바로 닫히지 않도록 새 cmd 창에서 실행
if not "%~1"=="_run_" (
    cmd /k "%~f0" _run_
    exit /b
)
setlocal
REM 한글 경로 깨짐 방지
chcp 65001 >nul

REM 현재 배치파일이 있는 폴더로 이동
cd /d "%~dp0"

echo.
echo ========================================
echo   AI 공약 멘토링 - 서버 실행 (집 노트북)
echo ========================================
echo.
echo 현재 폴더: %cd%
echo.

REM 1) 가상환경(.venv) 확인 및 사용
if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
    echo [확인] 가상환경 .venv 를 사용합니다.
) else (
    echo.
    echo [오류] .venv 가상환경을 찾을 수 없습니다.
    echo.
    echo 먼저 설치를 진행해 주세요:
    echo   1. '1_처음한번만_설치_집노트북.bat' 파일을 실행하세요
    echo   2. 설치가 완료된 후 이 파일을 다시 실행하세요
    echo.
    pause
    exit /b 1
)

echo 사용 Python: %PYEXE%
echo.

echo 서버를 시작합니다...
echo.
echo   주소: http://127.0.0.1:8000
echo   (브라우저에서 위 주소로 접속하세요)
echo.
echo   종료하려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으시면 됩니다.
echo ========================================
echo.

REM uvicorn으로 FastAPI 서버 실행
%PYEXE% -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

REM 서버가 종료되면 (오류 포함) 아래 메시지 표시
if errorlevel 1 (
    echo.
    echo ========================================
    echo [오류] 서버 실행 중 문제가 발생했습니다.
    echo ========================================
    echo.
    echo 가능한 원인:
    echo - uvicorn이 설치되지 않았습니다.
    echo   → '1_처음한번만_설치_집노트북.bat' 파일을 먼저 실행하세요
    echo - 8000번 포트가 이미 사용 중일 수 있습니다
    echo - 위쪽의 에러 메시지를 확인해 주세요
    echo.
)

pause

