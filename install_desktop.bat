@echo off
rem Roundtable desktop app (Windows, double-click once): install, then put Roundtable icons on the desktop
rem and in the Start menu. After that, open Roundtable from the icon: no black window (docs/DESKTOP.md).
rem Double-click it again to update (git pull + install).
setlocal
chcp 65001 >nul
cd /d "%~dp0" || goto :error
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\activate.bat" (
    echo [setup] Creating virtual environment .venv ...
    python -m venv .venv || goto :error
)
call ".venv\Scripts\activate.bat" || goto :error

echo [0/3] git pull ...
set GIT_TERMINAL_PROMPT=0
git pull || echo [warn] git pull failed, installing the local code.

echo [1/3] pip install -e ".[dev,windows,desktop]" ...
python -m pip install -e ".[dev,windows,desktop]"
if errorlevel 1 (
    echo [warn] The app window component ^(pywebview^) did not install: the icon will open an Edge app window instead.
    python -m pip install -e ".[dev,windows]" || goto :error
)

echo [2/3] Code sandbox ...
python scripts\setup_sandbox.py --check >nul 2>&1
if errorlevel 1 python scripts\setup_sandbox.py || echo [warn] Sandbox not installed: the python tool stays off.

echo [3/3] Creating icons ...
python scripts\install_desktop.py || goto :error
if not exist ".env" echo [warn] .env not found: copy .env.example to .env and fill in at least one API key.
echo.
echo Done. Close any running black Roundtable window, then double-click the new Roundtable icon on the desktop.
pause
exit /b 0

:error
echo.
echo [error] Install failed, see the messages above.
pause
exit /b 1
