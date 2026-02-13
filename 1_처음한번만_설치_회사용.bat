@echo off
REM 창이 바로 닫히지 않도록 새 cmd 창에서 실행 (끝나도 창 유지)
if not "%~1"=="_run_" (
    cmd /k "%~f0" _run_
    exit /b
)
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo ========================================
echo   AI 공약 멘토링 - 처음 한 번만 설치 [회사용]
echo ========================================
echo.
echo 현재 폴더: %cd%
echo.

REM Python 확인 (python 또는 py 시도)
set PYEXE=
where python >nul 2>&1 && set PYEXE=python
if not defined PYEXE where py >nul 2>&1 && set PYEXE=py
if not defined PYEXE (
    echo [오류] Python이 설치되어 있지 않거나 PATH에 없습니다.
    echo.
    echo 1. https://www.python.org/downloads/ 에서 Python 설치
    echo 2. 설치 시 "Add Python to PATH" 체크
    echo 3. 설치 후 컴퓨터 재시작 또는 명령 프롬프트 다시 열기
    echo.
    pause
    exit /b 1
)
echo 사용 중인 Python: %PYEXE%
%PYEXE% --version
echo.

if not exist .venv (
    echo [1/2] 가상환경 생성 중...
    %PYEXE% -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상환경 생성 실패.
        echo 위에 나온 오류 메시지를 확인하세요.
        echo.
        pause
        exit /b 1
    )
    echo 가상환경 생성 완료.
) else (
    echo [1/2] 가상환경 .venv 이미 있음.
)

echo.
echo [2/2] 패키지 설치 중...
.venv\Scripts\pip install -r requirements.txt
if errorlevel 1 (
    echo [오류] 패키지 설치 실패.
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   설치 끝. 이제 '2_서버실행_회사용.bat' 실행하세요.
echo ========================================
echo.
pause
