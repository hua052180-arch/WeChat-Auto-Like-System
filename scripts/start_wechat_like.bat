@echo off
setlocal EnableExtensions
chcp 65001 >nul

title WeChat Moments Like Manual Runner

set "WORKDIR=E:\wechat_like_helper\src"
set "SCRIPT=wechat_pc_moments_image_test.py"
set "PYTHON_EXE=C:\Users\Deple\anaconda3\python.exe"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo ========================================
echo 微信朋友圈点赞脚本 - 手动启动版
echo ========================================
echo 当前 bat 位置：%~dp0
echo 工作目录：%WORKDIR%
echo 脚本文件：%SCRIPT%
echo Python：%PYTHON_EXE%
echo.

echo [1] 检查工作目录...
cd /d "%WORKDIR%"
if errorlevel 1 (
    echo [错误] 无法进入目录：%WORKDIR%
    pause
    exit /b 1
)
echo [成功] 已进入目录：%CD%
echo.

echo [2] 检查 Python...
if not exist "%PYTHON_EXE%" (
    echo [错误] 没有找到 Python：%PYTHON_EXE%
    pause
    exit /b 1
)
echo [成功] 找到 Python。
echo.

echo [3] 检查脚本文件...
if not exist "%SCRIPT%" (
    echo [错误] 没有找到脚本：%WORKDIR%\%SCRIPT%
    pause
    exit /b 1
)
echo [成功] 找到脚本。
echo.

echo [4] 开始运行 Python 脚本...
echo ========================================
echo.

"%PYTHON_EXE%" -X utf8 -u "%SCRIPT%"

set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ========================================
echo Python 脚本已退出
echo Exit code: %EXIT_CODE%
echo ========================================
echo.

pause
exit /b %EXIT_CODE%