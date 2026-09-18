@echo off
setlocal EnableExtensions
chcp 65001 >nul

title Stop Double WeChat Like

echo ========================================
echo Stop Double WeChat Like Tasks
echo ========================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$patterns=@('wechat_like_worker_by_pid.py','wechat_like_auto_pid_launcher.py','wechat_multi_process_manager.py','wechat_worker_','wechat_pc_moments_image_test.py','wechat_like_pid_74672_click.bat','wechat_like_pid_47764_click.bat'); $procs=Get-CimInstance Win32_Process | Where-Object { $cmd=$_.CommandLine; ($_.Name -like 'python*' -or $_.Name -eq 'cmd.exe') -and ($patterns | Where-Object { $cmd -like ('*' + $_ + '*') }) }; if($procs){ $procs | ForEach-Object { Write-Host ('Stop PID=' + $_.ProcessId + ' Name=' + $_.Name); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Host 'No double WeChat like task found.' }"

echo.
echo ========================================
echo Stop finished.
echo ========================================

exit /b 0
