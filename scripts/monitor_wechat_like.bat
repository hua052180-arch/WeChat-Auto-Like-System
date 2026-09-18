@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0monitor_wechat_like.ps1"
exit /b %errorlevel%
