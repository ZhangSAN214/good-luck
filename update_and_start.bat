@echo off
rem Roundtable: update, check models, start (Windows, double-click). Stops at the first error; the window stays open.
setlocal
chcp 65001 >nul
cd /d "%~dp0" || goto :error
set PYTHONIOENCODING=utf-8
rem Never wait for a hidden password prompt
set GIT_TERMINAL_PROMPT=0
set GCM_INTERACTIVE=never

echo [1/5] Activating .venv ...
if not exist ".venv\Scripts\activate.bat" (
    echo [error] .venv not found. Run start.bat once to create it.
    goto :error
)
call ".venv\Scripts\activate.bat" || goto :error

echo [2/5] git pull ...
git pull || goto :error

echo [3/5] pip install -e ".[dev,media]" ...
python -m pip install -e ".[dev,media]" || goto :error

echo [4/5] check_models.py ^(saved to check_result.txt^) ...
python scripts\check_models.py > check_result.txt 2>&1
set CHECK_RC=%errorlevel%
type check_result.txt
rem 1 = a model ID does not exist (calls would fail): stop. 2 = could not reach the model list: warn only.
rem Price changes are only warnings inside check_models.py and do not stop the start.
if "%CHECK_RC%"=="1" (
    echo [error] check_models.py: a model ID was not found ^(exit code 1^), see check_result.txt
    goto :error
)
if not "%CHECK_RC%"=="0" echo [warn] check_models.py could not check the models ^(exit code %CHECK_RC%^), starting anyway.

echo [5/5] Stopping an old server on port 8000, if any ...
set "OLD="
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /r /c:":8000 .*LISTENING"') do set "OLD=%%p"
if defined OLD (
    tasklist /fi "pid eq %OLD%" /nh | findstr /i "python" >nul || (
        echo [error] Port 8000 is used by another program ^(PID %OLD%^), not by Roundtable.
        goto :error
    )
    echo [info] Stopping the old Roundtable server ^(PID %OLD%^) ...
    taskkill /pid %OLD% /t /f >nul
    timeout /t 2 /nobreak >nul
)
echo Starting server at http://127.0.0.1:8000  (Ctrl+C to stop)
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8000"
python -m uvicorn roundtable.api.app:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto :error
echo.
echo Server stopped.
pause
exit /b 0

:error
echo.
echo [error] Stopped, see the messages above.
pause
exit /b 1
