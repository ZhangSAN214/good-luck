@echo off
rem Roundtable: update, check models, start (Windows, double-click). Stops at the first error; the window stays open.
setlocal
chcp 65001 >nul
cd /d "%~dp0" || goto :error
set PYTHONIOENCODING=utf-8

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
if not "%CHECK_RC%"=="0" (
    echo [error] check_models.py failed ^(exit code %CHECK_RC%^), see check_result.txt
    goto :error
)

echo [5/5] Starting server at http://127.0.0.1:8000  (Ctrl+C to stop)
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
