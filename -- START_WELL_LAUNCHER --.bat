@echo off
title WELL Launcher v2
color 0B
cls
echo.
echo  ================================================
echo         WELL Launcher v2
echo  ================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo  [ERROR] Python not found!
    echo  Install Python from https://python.org
    pause & exit /b
)

cd /d "%~dp0"

if not exist "%~dp0static\index.html" (
    color 0C
    echo  [ERROR] static\index.html not found!
    echo  Run INSTALL.bat first, then put index.html in the static\ folder
    pause & exit /b
)

echo  Starting server...
start /b python server.py

echo.
powershell -NoProfile -Command "for ($i=5; $i -ge 1; $i--) { Write-Host \"`r  Opening browser in: $i \" -NoNewline; Start-Sleep 1 }; Write-Host '  Browser opened!          '"

start "" http://localhost:9000
