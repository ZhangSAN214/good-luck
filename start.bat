@echo off
rem Roundtable one-click start (Windows): update code, install, run server, open browser.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [setup] Creating virtual environment .venv ...
    python -m venv .venv || goto :error
)
call ".venv\Scripts\activate.bat" || goto :error

echo [1/3] git pull ...
git pull || echo [warn] git pull failed, starting with the local code.

echo [2/3] pip install -e ".[dev]" ...
python -m pip install -e ".[dev]" || goto :error

if not exist ".env" echo [warn] .env not found: copy .env.example to .env and fill in at least one API key.

echo [3/3] Starting server at http://127.0.0.1:8000  (Ctrl+C to stop)
set PYTHONIOENCODING=utf-8
rem Open the browser a few seconds later, once the server is up
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://127.0.0.1:8000"
python -m uvicorn roundtable.api.app:app --host 127.0.0.1 --port 8000
goto :eof

:error
echo.
echo [error] Startup failed, see the messages above.
pause
exit /b 1
