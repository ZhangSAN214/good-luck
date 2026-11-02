@echo off
rem Live2D preview: serves web\ on 127.0.0.1 and opens the player page.
rem Cubism Core is loaded from the official Live2D site at run time (needs internet).
rem Put the models in web\live2d\models\ (not in git): copy the live2d_out folder
rem from the assets/claude-live2d branch there.
rem Usage: double-click (PSD2Live version), or "serve.bat image2live2d".

cd /d "%~dp0\.."
if exist "..\.venv\Scripts\activate.bat" call "..\.venv\Scripts\activate.bat"

set VERSION=%1
if "%VERSION%"=="" set VERSION=psd2live
set MODEL=models/live2d_out/psd2live/psd2live.model3.json
if /i "%VERSION%"=="image2live2d" set MODEL=models/live2d_out/image2live2d/model/model.model3.json

if not exist "live2d\%MODEL:/=\%" (
  echo Model not found: web\live2d\%MODEL:/=\%
  echo Copy the live2d_out folder from the assets/claude-live2d branch into web\live2d\models\
  pause
  exit /b 1
)

set PORT=8765
start "" "http://127.0.0.1:%PORT%/live2d/index.html?model=%MODEL%"
python -m http.server %PORT% --bind 127.0.0.1
