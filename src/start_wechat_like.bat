@echo off
setlocal EnableExtensions
chcp 65001 >nul

title WeChat Moments Like Runner

set "WORKDIR=E:\wechat_like_helper\src"
set "SCRIPT=wechat_pc_moments_image_test.py"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d "%WORKDIR%"
if errorlevel 1 (
    echo [ERROR] Cannot enter workdir: %WORKDIR%
    pause
    exit /b 1
)

echo ========================================
echo Start WeChat Moments Like Script
echo Workdir: %WORKDIR%
echo Script: %SCRIPT%
echo ========================================
echo.

python -X utf8 -u "%SCRIPT%"

set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ========================================
echo Script finished
echo Exit code: %EXIT_CODE%
echo ========================================
echo.

pause
exit /b %EXIT_CODE%