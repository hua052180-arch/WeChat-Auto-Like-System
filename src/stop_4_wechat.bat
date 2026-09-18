@echo off
set "PYTHON_EXE=%USERPROFILE%\anaconda3\python.exe"
set "PYTHONPATH=%~dp0..\vendor;%PYTHONPATH%"
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo 停止所有4x微信点赞worker
echo 时间：%date% %time%
echo ========================================

"%PYTHON_EXE%" "launch_4_wechat.py" --stop

echo.
echo 已停止所有worker。
pause
