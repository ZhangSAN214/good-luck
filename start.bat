@echo off
rem Roundtable one-click start (Windows): update code, install, run server, open browser.
rem Double-click this file to start; close the window or press Ctrl+C to stop. stop.bat stops a server left running in the background.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem Never wait for a password prompt (a hidden credential window looked like a hang): fail and start with the local code instead.
set GIT_TERMINAL_PROMPT=0
set GCM_INTERACTIVE=never
set PYTHONIOENCODING=utf-8
rem stop.bat leaves this flag so the window it stopped closes quietly instead of reporting a failure
set "STOP_FLAG=%TEMP%\roundtable-stopped.flag"

if not exist ".venv\Scripts\activate.bat" (
    echo [setup] Creating virtual environment .venv ...
    python -m venv .venv || goto :error
)
call ".venv\Scripts\activate.bat" || goto :error

echo [1/4] git pull ...
git pull --ff-only || echo [warn] git pull failed, starting with the local code.

echo [2/4] pip install -e ".[dev,windows]" ...
python -m pip install -e ".[dev,windows]" || goto :error

rem Code sandbox for the python tool (one-time download, then offline)
python scripts\setup_sandbox.py --check >nul 2>&1
if errorlevel 1 (
    echo [setup] Installing the code sandbox ^(Deno + Pyodide, one time^) ...
    python scripts\setup_sandbox.py || echo [warn] Sandbox not installed: the python tool stays off.
)

if not exist ".env" echo [warn] .env not found: copy .env.example to .env and fill in at least one API key.

echo [3/4] Stopping an old server on port 8000, if any ...
call :stop_old || goto :error

echo [4/4] Starting server at http://127.0.0.1:8000  (Ctrl+C or close this window to stop)
rem Open the browser a few seconds later, once the server is up
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8000"
del "%STOP_FLAG%" >nul 2>&1
python -m uvicorn roundtable.api.app:app --host 127.0.0.1 --port 8000
if exist "%STOP_FLAG%" exit /b 0
if errorlevel 1 goto :error
exit /b 0

rem Stop whatever python process listens on 127.0.0.1:8000 (an old window or a background start).
rem Anything else on that port is left alone and reported.
:stop_old
set "OLD="
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /r /c:":8000 .*LISTENING"') do set "OLD=%%p"
if not defined OLD exit /b 0
tasklist /fi "pid eq %OLD%" /nh | findstr /i "python" >nul
if errorlevel 1 (
    echo [error] Port 8000 is used by another program ^(PID %OLD%^), not by Roundtable. Close it and try again.
    exit /b 1
)
echo [info] Stopping the old Roundtable server ^(PID %OLD%^) ...
type nul > "%STOP_FLAG%"
taskkill /pid %OLD% /t /f >nul
timeout /t 2 /nobreak >nul
exit /b 0

:error
echo.
echo [error] Startup failed, see the messages above.
pause
exit /b 1
