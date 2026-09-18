@echo off
chcp 65001 >nul

echo ========================================
echo 停止微信朋友圈点赞脚本
echo 时间：%date% %time%
echo ========================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ps = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*wechat_pc_moments_image_test.py*' }; if ($ps) { $ps | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host '已停止微信点赞脚本' } else { Write-Host '没有找到正在运行的微信点赞脚本' }"

exit