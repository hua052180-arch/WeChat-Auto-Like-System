@echo off
chcp 65001 >nul
title WeChat Like PID 54052

cd /d "E:\wechat_like_helper\src"

"C:\Users\Deple\anaconda3\python.exe" -X utf8 -u "E:\wechat_like_helper\src\wechat_like_worker_by_pid.py" --pid 54052 --main -1900 20 900 520 --moments -970 20 560 520

echo.
echo ========================================
echo PID 54052 task exited.
echo ========================================
pause
