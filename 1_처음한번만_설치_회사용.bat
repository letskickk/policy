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

REM Python 확인: py -3 우선, 그 다음 WindowsApps 제외한 python.exe 자동 선택
set "PYEXE="

REM 1) Python Launcher (권장)
REM - 패키지 호환성 때문에 3.12/3.11/3.10을 우선 사용합니다.
where py >nul 2>&1
if %errorlevel%==0 (
    for %%V in (3.12 3.11 3.10) do (
        py -%%V --version >nul 2>&1
        if not errorlevel 1 (
            set "PYEXE=py -%%V"
            goto PY_FOUND
        )
    )
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        set "PYEXE=py -3"
        goto PY_FOUND
    )
    py --version >nul 2>&1
    if not errorlevel 1 (
        set "PYEXE=py"
        goto PY_FOUND
    )
)

REM 2) 흔한 사용자 설치 경로 우선 (WindowsApps 영향 없음)
if not defined PYEXE (
    if exist "%LocalAppData%\Python\bin\python.exe" (
        "%LocalAppData%\Python\bin\python.exe" -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PYEXE=\"%LocalAppData%\Python\bin\python.exe\""
    )
)

REM 3) python.exe 경로 중 WindowsApps(스토어 별칭) 제외
if not defined PYEXE (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        echo %%P | find /i "WindowsApps" >nul
        if errorlevel 1 (
            "%%P" -c "import sys" >nul 2>&1
            if not errorlevel 1 (
                set "PYEXE="%%P""
                goto PY_FOUND
            )
        )
    )
)
:PY_FOUND

if not defined PYEXE (
    echo [오류] 사용 가능한 Python을 찾을 수 없습니다.
    echo.
    echo [방법] 아래 둘 중 하나를 한 번만 처리하세요
    echo   1. python.org 에서 Python 3.12 권장 설치 + Add Python to PATH 체크
    echo   2. 설정 - 앱 - 고급 앱 설정 - 앱 실행 별칭에서 python.exe/python3.exe 끔
    echo.
    pause
    exit /b 1
)
echo 사용 중인 Python = %PYEXE%
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
