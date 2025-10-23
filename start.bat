@echo off
TITLE ScanBot Launcher

:: 1. Change directory to the batch file's location
cd /d %~dp0

:: 2. Start the Flask web app (web_app.py)
echo [1/3] Starting Flask (web_app.py) server...
START "ScanBot Server" python web_app.py

:: 3. Wait 5 seconds for the server to initialize
echo [2/3] Waiting 5 seconds for the server to initialize...
timeout /t 5 /nobreak > nul

:: 4. Start ngrok for port 5000
echo [3/3] Starting ngrok (port 5000)...
START "ngrok" ngrok.exe http 5000

echo.
echo All programs have been started in new windows.
echo You can close this window.
pause