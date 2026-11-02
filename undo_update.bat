@echo off
rem Roundtable: undo the last update (docs/SAFE_UPDATE.md). Stops the server, puts the code back to the version
rem before the last update, and if that update upgraded the database, restores the "update" checkpoint
rem (a checkpoint of the current data is saved first). The undone version is not installed again automatically.
setlocal
chcp 65001 >nul
cd /d "%~dp0" || goto :error
set PYTHONIOENCODING=utf-8
set GIT_TERMINAL_PROMPT=0
set GCM_INTERACTIVE=never
set "STOP_FLAG=%TEMP%\roundtable-stopped.flag"
if not exist ".venv\Scripts\activate.bat" (
    echo [error] .venv not found. Run start.bat once to create it.
    goto :error
)
call ".venv\Scripts\activate.bat" || goto :error

echo [1/3] Stopping the Roundtable server on port 8000, if any ...
set "OLD="
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /r /c:":8000 .*LISTENING"') do set "OLD=%%p"
if defined OLD (
    tasklist /fi "pid eq %OLD%" /nh | findstr /i "python" >nul || (
        echo [error] Port 8000 is used by another program ^(PID %OLD%^), not by Roundtable.
        goto :error
    )
    type nul > "%STOP_FLAG%"
    taskkill /pid %OLD% /t /f >nul
    timeout /t 2 /nobreak >nul
)

echo [2/3] Undoing the last update ...
python scripts\safe_update.py undo || goto :error

echo [3/3] Reinstalling the previous version ...
python -m pip install -e ".[dev,windows]" || goto :error
echo.
echo Done. Double-click start.bat to open Roundtable again.
pause
exit /b 0

:error
echo.
echo [error] Could not undo, see the messages above. Nothing else was changed.
pause
exit /b 1
