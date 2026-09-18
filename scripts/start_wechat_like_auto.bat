@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "WORKDIR=E:\wechat_like_helper\src"
set "SCRIPT=wechat_pc_moments_image_test.py"
set "PYTHON_EXE=C:\Users\Deple\anaconda3\python.exe"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d "%WORKDIR%"
if errorlevel 1 exit /b 1

if not exist "logs" mkdir "logs"

set "LOG_FILE=%WORKDIR%\logs\wechat_like_auto.log"

echo ======================================== >> "%LOG_FILE%"
echo Auto Start WeChat Moments Like Script >> "%LOG_FILE%"
echo Time: %DATE% %TIME% >> "%LOG_FILE%"
echo Workdir: %WORKDIR% >> "%LOG_FILE%"
echo Script: %SCRIPT% >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

"%PYTHON_EXE%" -X utf8 -u "%SCRIPT%" >> "%LOG_FILE%" 2>&1

exit /b %ERRORLEVEL%
