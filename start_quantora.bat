@echo off
rem ===========================================================================
rem  Quantora - start all running engines, each in its own window.
rem  Before this: MT5 open and logged in, Oracle running, Qauntora\.env filled in.
rem  To stop an engine: click its window and press Ctrl+C (or close the window).
rem  See TESTING_GUIDE.md, part 9.
rem ===========================================================================
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
set PYTHONIOENCODING=utf-8

if not exist "%PY%" (
    echo Could not find %PY%
    echo Create the virtual environment first - TESTING_GUIDE.md, part 2.
    pause
    exit /b 1
)
if not exist "%ROOT%.env" (
    echo Could not find %ROOT%.env
    echo Copy .env.example to .env and fill it in - TESTING_GUIDE.md, part 3.
    pause
    exit /b 1
)

echo Starting Quantora Feed ...
start "Quantora Feed" /D "%ROOT%quantora_feed" cmd /k ""%PY%" main.py run"
rem give Feed a head start so the first bars are stored before Core looks
timeout /t 20 /nobreak >nul

echo Starting Quantora Core ...
start "Quantora Core" /D "%ROOT%quantora_core" cmd /k ""%PY%" main.py run"

echo Starting Quantora Validate ...
start "Quantora Validate" /D "%ROOT%quantora_validate" cmd /k ""%PY%" main.py run"

echo Starting Quantora Notify ...
start "Quantora Notify" /D "%ROOT%quantora_notify" cmd /k ""%PY%" main.py run"

echo.
echo All four engines started in separate windows. You can close this one.
timeout /t 10 >nul
endlocal
