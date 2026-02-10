@echo off
REM 프로젝트 경로: 이 배치 파일이 있는 폴더(로컬, 예: c:\policy)
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else (
    echo .venv 없음. 먼저 실행: python -m venv .venv
    echo 그 다음: .venv\Scripts\activate
    echo        pip install -r requirements.txt
    pause
    exit /b 1
)
echo 서버 시작: http://127.0.0.1
echo API 문서: http://127.0.0.1/docs
uvicorn backend.main:app --reload --host 127.0.0.1 --port 80
pause
