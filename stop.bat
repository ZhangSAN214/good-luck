@echo off
rem Roundtable: stop the server on port 8000 (for example one started in the background without a window).
setlocal
chcp 65001 >nul
rem Tells the start.bat window being stopped to close quietly instead of reporting a failure
set "STOP_FLAG=%TEMP%\roundtable-stopped.flag"
set "OLD="
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /r /c:":8000 .*LISTENING"') do set "OLD=%%p"
if not defined OLD (
    echo No server is running on port 8000.
    goto :done
)
tasklist /fi "pid eq %OLD%" /nh | findstr /i "python" >nul
if errorlevel 1 (
    echo Port 8000 is used by another program ^(PID %OLD%^), not by Roundtable. Left it alone.
    goto :done
)
type nul > "%STOP_FLAG%"
taskkill /pid %OLD% /t /f >nul && echo Stopped the Roundtable server ^(PID %OLD%^).
:done
timeout /t 5
