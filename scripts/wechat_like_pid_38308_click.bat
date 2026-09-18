@echo off
chcp 65001 >nul
title WeChat Like PID 38308

cd /d "E:\wechat_like_helper\src"

"C:\Users\Deple\anaconda3\python.exe" -X utf8 -u "E:\wechat_like_helper\src\wechat_like_worker_by_pid.py" --pid 38308 --main -1900 560 900 500 --moments -970 560 560 500

echo.
echo ========================================
echo PID 38308 task exited.
echo ========================================
pause
