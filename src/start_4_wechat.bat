@echo off
set "PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe"
set "PYTHONPATH=%~dp0..\vendor;%PYTHONPATH%"
chcp 65001 >nul

cd /d "%~dp0"

if not exist "logs\wechat_4x" mkdir "logs\wechat_4x"

echo ========================================
echo 启动2个微信朋友圈点赞（右侧竖屏）
echo 时间：%date% %time%
echo ========================================
echo.

"%PYTHON_EXE%" "launch_4_wechat.py" >> "logs\wechat_4x\launch_%date:~0,4%%date:~5,2%%date:~8,2%.log" 2>&1
