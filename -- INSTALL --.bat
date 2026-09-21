@echo off
title WELL Launcher v2 - Installer
color 0B & cls
echo.
echo  ================================================
echo         WELL Launcher v2 - INSTALLER
echo  ================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C & echo  [ERROR] Python not found!
    echo  Install Python from https://python.org
    pause & exit /b
)

echo  Installing dependencies...
echo.
if not exist "%~dp0requirements.txt" (
    color 0C
    echo  [ERROR] requirements.txt not found!
    echo  Make sure you are running this installer from the full repository.
    pause & exit /b
)
python -m pip install -r "%~dp0requirements.txt"
echo.

:: Create the static folder if it doesn't exist yet
if not exist "%~dp0static\" (
    mkdir "%~dp0static"
    echo  [OK] Created static\ folder.
)

:: Check that index.html already exists in static/
if not exist "%~dp0static\index.html" (
    color 0E
    echo  [!] WARNING: static\index.html is missing!
    echo  [!] Put the index.html file into the static\ folder
    echo      before running the launcher.
    color 0B
)

echo.
color 0A
echo  ================================================
echo   [OK] Installation complete! Run START_WELL_LAUNCHER.bat
echo  ================================================
echo.
pause
