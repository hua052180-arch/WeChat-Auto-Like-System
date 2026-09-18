@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Double WeChat Like Auto PID Starter

set "PYTHON_EXE=C:\Users\Deple\anaconda3\python.exe"
set "AUTO_STARTER=%~dp0auto_start_wechat_like_by_pid.py"

echo ========================================
echo Double WeChat Like Auto PID Starter
echo ========================================
echo.
echo Current folder:
echo %~dp0
echo.

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python not found:
    echo %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

if not exist "%AUTO_STARTER%" (
    echo [ERROR] Auto starter not found:
    echo %AUTO_STARTER%
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -X utf8 -u "%AUTO_STARTER%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ========================================
echo Auto PID starter exited. Code: %EXIT_CODE%
echo ========================================
echo.
pause
exit /b %EXIT_CODE%
