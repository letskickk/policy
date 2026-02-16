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

REM Python 확인 (단순 버전: py -3만 사용)
set "PYEXE=py -3"
%PYEXE% --version >nul 2>&1
if errorlevel 1 (
    echo [오류] py -3 를 실행할 수 없습니다.
    echo python.org 에서 Python 설치 후 다시 실행해 주세요.
    pause
    exit /b 1
)
echo 사용 중인 Python = %PYEXE%
%PYEXE% --version
echo.

REM 가상환경 확인 및 생성
if not exist .venv goto :create_venv

REM 가상환경이 있으면 동작 확인
echo [1/3] 가상환경 .venv 이미 있음. 동작 확인 중...
.venv\Scripts\python.exe --version >nul 2>&1
if not errorlevel 1 goto :venv_ok

REM 가상환경이 깨져 있으면 삭제 후 재생성
echo.
echo 기존 .venv 가 깨져 있습니다. 삭제 후 다시 만듭니다.
echo (삭제 중 잠시 기다려 주세요...)
echo.

REM 삭제 전에 잠시 대기
timeout /t 1 /nobreak >nul 2>&1

REM 삭제 실행
rmdir /s /q .venv 2>nul
timeout /t 1 /nobreak >nul 2>&1

REM 삭제 확인
if exist .venv (
    echo.
    echo [경고] .venv 폴더가 완전히 삭제되지 않았습니다.
    echo 수동으로 삭제 후 다시 실행해 주세요:
    echo   1. 이 창을 닫으세요
    echo   2. 탐색기에서 .venv 폴더를 삭제하세요
    echo   3. 이 배치 파일을 다시 실행하세요
    echo.
    pause
    exit /b 1
)

:create_venv
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
goto :venv_done

:venv_ok
echo 가상환경 정상 동작 확인됨.

:venv_done

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
