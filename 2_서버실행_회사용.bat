@echo off
REM ========== 회사용 서버 실행 ==========
REM 회사 컴퓨터 환경에 맞춘 설정 (포트 8001, py launcher 사용 시 대비)
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo .venv이 없습니다. 먼저 '1_처음한번만_설치.bat' 을 실행하세요.
    pause
    exit /b 1
)

echo [회사용] 서버 시작 중...
echo.
echo   페이지 주소: http://127.0.0.1:8001
echo   (브라우저에서 이 주소로 접속하세요)
echo.
echo 종료하려면 이 창을 닫거나 Ctrl+C 하세요.
echo ========================================
echo.

.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001

if errorlevel 1 (
    echo.
    echo 오류가 났습니다. 8001번 포트가 이미 쓰이는지 확인해 보세요.
)
pause
