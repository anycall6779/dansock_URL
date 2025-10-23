@echo off
TITLE Python Package Installer

echo [1/3] Changing directory to the script location...
:: This batch file's directory
cd /d %~dp0

echo [2/3] Starting installation from requirements.txt...
echo This may take a few minutes...
echo.

:: Run pip install command
python -m pip install -r requirements.txt

echo.
echo [3/3] Installation complete (or updated).
echo You can close this window.
echo.
pause