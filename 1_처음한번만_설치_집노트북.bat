@echo off
REM 집 노트북 환경용 - 처음 한 번만 설치
REM (회사 PC용은 1_처음한번만_설치.bat 사용)
if not "%~1"=="_run_" (
    cmd /k "%~f0" _run_
    exit /b
)
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo ========================================
echo   AI 공약 멘토링 - 처음 한 번만 설치
echo   (집 노트북용)
echo ========================================
echo.
echo 현재 폴더: %cd%
echo.

REM Python 확인 (py -3 우선 → python → py)
set PYEXE=
where py >nul 2>&1 && set PYEXE=py -3
if not defined PYEXE where python >nul 2>&1 && set PYEXE=python
if not defined PYEXE where py >nul 2>&1 && set PYEXE=py
if not defined PYEXE (
    echo [오류] Python이 설치되어 있지 않거나 PATH에 없습니다.
    echo.
    echo 1. https://www.python.org/downloads/ 에서 Python 3.10 이상 설치
    echo 2. 설치 시 "Add Python to PATH" 반드시 체크
    echo 3. 설치 후 터미널 다시 열고 이 파일 다시 실행
    echo.
    pause
    exit /b 1
)
echo 사용 중인 Python: %PYEXE%
%PYEXE% --version
echo.

if not exist .venv (
    echo [1/3] 가상환경 생성 중...
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
    echo [1/3] 가상환경 .venv 이미 있음. 동작 확인 중...
    .venv\Scripts\python.exe --version >nul 2>&1
    if errorlevel 1 (
        echo 기존 .venv 가 깨져 있습니다. 삭제 후 다시 만듭니다.
        rmdir /s /q .venv
        %PYEXE% -m venv .venv
        if errorlevel 1 (
            echo [오류] 가상환경 재생성 실패.
            pause
            exit /b 1
        )
        echo 가상환경 재생성 완료.
    )
)

echo.
echo [2/3] 가상환경에 pip 설치 확인 중...
.venv\Scripts\python.exe -m pip --version >nul 2>&1
if errorlevel 1 (
    echo pip이 없어 ensurepip로 설치합니다...
    .venv\Scripts\python.exe -m ensurepip --upgrade
    if errorlevel 1 (
        echo [오류] pip 설치 실패. Python 3.10~3.13 설치를 권장합니다.
        echo https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo pip 설치 완료.
) else (
    echo pip 있음.
)

echo.
echo [3/3] 패키지 설치 중... (faiss 등 첫 설치 시 5~15분 걸릴 수 있습니다. 멈춘 것처럼 보여도 기다려 주세요.)
echo.
.venv\Scripts\python.exe -m pip install -r requirements.txt -v
if errorlevel 1 (
    echo.
    echo [오류] 패키지 설치 실패.
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   설치 끝. 이제 '2_서버실행.bat' 실행하세요.
echo ========================================
echo.
pause
