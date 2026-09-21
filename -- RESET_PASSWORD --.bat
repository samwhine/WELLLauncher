@echo off
title WELL Launcher v2 - Reset Password
color 0E & cls
echo.
echo  ================================================
echo         WELL Launcher v2 - RESET PASSWORD
echo  ================================================
echo.
echo  Use this if you forgot your login password.
echo  You don't need to stop the server first.
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C & echo  [ERROR] Python not found!
    echo  Install Python from https://python.org
    pause & exit /b
)

cd /d "%~dp0"

if not exist "%~dp0users.json" (
    color 0C
    echo  [ERROR] users.json not found!
    echo  Make sure this file is in the same folder as server.py
    pause & exit /b
)

python server.py --reset-password
