$ErrorActionPreference = "SilentlyContinue"

$ScriptKey = "wechat_pc_moments_image_test.py"
$StartBat  = "C:\Users\Deple\Desktop\程序\微信\start_wechat_like.bat"
$LogDir    = "C:\Users\Deple\Desktop\程序\微信\logs"
$LogFile   = Join-Path $LogDir "wechat_like_monitor.log"

if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-Log($msg) {
    Add-Content -Path $LogFile -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg) -Encoding UTF8
}

Write-Log "================ monitor check start ================"

$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -like "*$ScriptKey*"
}

$count = @($procs).Count
Write-Log "wechat like process count: $count"

if ($count -eq 0) {
    if (Test-Path $StartBat) {
        Write-Log "not running, start program: $StartBat"
        Start-Process -FilePath $StartBat -WorkingDirectory (Split-Path $StartBat)
        exit 0
    } else {
        Write-Log "ERROR: start bat not found: $StartBat"
        exit 2
    }
}

if ($count -eq 1) {
    Write-Log "running ok, PID: $($procs.ProcessId)"
    exit 0
}

Write-Log "ERROR: too many wechat like processes, kill all and restart."

foreach ($p in $procs) {
    Write-Log "kill PID: $($p.ProcessId)"
    Stop-Process -Id $p.ProcessId -Force
}

Start-Sleep -Seconds 5

if (Test-Path $StartBat) {
    Write-Log "restart program: $StartBat"
    Start-Process -FilePath $StartBat -WorkingDirectory (Split-Path $StartBat)
    exit 0
} else {
    Write-Log "ERROR: start bat not found after kill: $StartBat"
    exit 2
}
