@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "SCRIPT=wechat_pc_moments_image_test.py"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ps = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*%SCRIPT%*' }; if ($ps) { $ps | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host 'Stopped.' } else { Write-Host 'Not running.' }"

exit /b 0