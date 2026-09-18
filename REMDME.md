# 微信朋友圈与账号切换项目启动说明

## 一、项目文件

请确认以下文件位于同一目录：

```text
E:\wechat_like_helper\src\comment_like.py
E:\wechat_like_helper\src\switch_wechat_account.py
```

当前真机配置：

```text
设备序列号：7bfc6f84
ADB端口：5038
手机型号：OnePlus 9R / LE2100
```

## 二、启动前准备

1. MuMu 模拟器保持正常运行，不需要关闭。
2. 用 USB 数据线连接真机。
3. 手机保持解锁。
4. 开启“开发者选项”和“USB调试”。
5. USB用途选择“传输文件”。
6. 手机弹出USB调试授权时点击“允许”。

## 三、启动 scrcpy 投屏

打开第一个 PowerShell，执行：

```powershell
cd "C:\Users\Deple\Desktop\scrcpy-win64-v4.0\scrcpy-win64-v4.0"

Remove-Item Env:ADB_SERVER_SOCKET -ErrorAction SilentlyContinue

$env:ADB_SERVER_SOCKET="tcp:127.0.0.1:5038"

.\adb.exe -P 5038 devices -l

.\scrcpy.exe -s 7bfc6f84
```

设备列表中必须出现：

```text
7bfc6f84    device    model:LE2100
```

注意：

* 不要使用5037。
* 不要使用5039。
* 不要关闭MuMu。
* 不要执行 `Get-Process adb | Stop-Process -Force`。
* `ERROR:` 开头的内容是报错输出，不是PowerShell命令。

## 四、启动朋友圈程序

打开第二个 PowerShell，执行：

```powershell
cd "E:\wechat_like_helper\src"

python .\comment_like.py
```

程序会自动：

1. 连接USB真机；
2. 打开微信；
3. 进入“发现”；
4. 进入朋友圈；
5. 使用ADB截图识别“··”；
6. 截图判断菜单显示“赞”还是“取消”；
7. 最多检查15条；
8. 检测到“取消”时退出朋友圈。

## 五、启动账号切换程序

朋友圈程序结束后，在第二个 PowerShell 中执行：

```powershell
cd "E:\wechat_like_helper\src"

python .\switch_wechat_account.py
```

程序会：

1. 点击微信底部“我”；
2. 从截图中识别当前微信号；
3. 点击“设置”；
4. 滑动到设置页面底部；
5. 点击“切换账号”；
6. 识别账号卡片里的微信号；
7. 排除已经进入过的账号；
8. 切换到下一个账号；
9. 切换期间等待微信加载，不连续按返回键。

账号记录保存在：

```text
E:\wechat_like_helper\data\visited_wechat_accounts.txt
```

清空账号访问记录：

```powershell
Remove-Item "E:\wechat_like_helper\data\visited_wechat_accounts.txt" -Force
```

## 六、完整运行顺序

第一个 PowerShell：

```powershell
cd "C:\Users\Deple\Desktop\scrcpy-win64-v4.0\scrcpy-win64-v4.0"
Remove-Item Env:ADB_SERVER_SOCKET -ErrorAction SilentlyContinue
$env:ADB_SERVER_SOCKET="tcp:127.0.0.1:5038"
.\scrcpy.exe -s 7bfc6f84
```

第二个 PowerShell：

```powershell
cd "E:\wechat_like_helper\src"
python .\comment_like.py
```

朋友圈程序结束后：

```powershell
python .\switch_wechat_account.py
```

切换成功后再次运行：

```powershell
python .\comment_like.py
```

## 七、首次运行安装依赖

```powershell
python -m pip install opencv-python numpy pytesseract
```

验证 pytesseract：

```powershell
python -c "import pytesseract; print(pytesseract.__version__)"
```

检查 Tesseract：

```powershell
Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

应返回：

```text
True
```

## 八、真机连接检查

```powershell
cd "C:\Users\Deple\Desktop\scrcpy-win64-v4.0\scrcpy-win64-v4.0"

.\adb.exe -P 5038 devices -l
```

必须看到：

```text
7bfc6f84    device
```

没有出现时：

1. 保持手机解锁；
2. 重新拔插数据线；
3. USB用途选择“传输文件”；
4. 确认USB调试开启；
5. 在手机上重新允许USB调试。

## 九、电脑微信朋友圈自动点赞补充

当前已经新增一套 **电脑微信朋友圈自动点赞方案**，不依赖手机真机，也不依赖 MuMu 模拟器。

### 1. 项目文件

请确认以下文件位于同一目录：

```text
E:\wechat_like_helper\src\wechat_pc_moments_image_test.py
```

调试图片目录：

```text
E:\wechat_like_helper\src\debug_pc_wechat
```

主要调试图片包括：

```text
raw_wechat_capture.png
more_button_detected.png
after_click_more_menu.png
cancel_like_check.png
like_button_relative_detected.png
after_click_like.png
moments_template_match.png
```

### 2. 当前实现功能

电脑微信版本目前已经实现：

1. 自动识别并激活电脑微信窗口；
2. 自动点击左侧朋友圈入口；
3. 自动识别朋友圈独立窗口；
4. 使用截图识别朋友圈右侧“··”按钮；
5. 后台点击“··”，不占用真实鼠标；
6. 判断弹出的菜单是“赞”还是“取消”；
7. 如果是“赞”，自动点赞；
8. 如果是“取消”，说明已经点赞过，不会再次点击，避免取消赞；
9. 本轮内会记录已经处理过的朋友圈，避免同一条重复点击；
10. 点满 30 个赞后，关闭朋友圈窗口；
11. 休息 30 分钟；
12. 休息结束后重新进入朋友圈，开始下一轮；
13. 支持后台滚动，不移动真实鼠标；
14. 支持调试截图，方便判断识别位置是否正确。

### 3. 当前运行逻辑

当前逻辑是：

```text
进入电脑微信
↓
点击朋友圈
↓
进入朋友圈独立窗口
↓
慢速滚动查找“··”
↓
点击“··”
↓
判断菜单状态
↓
如果是“赞” → 点击赞
↓
如果是“取消” → 跳过，不取消赞
↓
直到本轮点赞满 30 个
↓
关闭朋友圈
↓
休息 30 分钟
↓
重新进入朋友圈
↓
开始下一轮
```

### 4. 运行电脑微信点赞程序

打开 PowerShell，执行：

```powershell
cd "E:\wechat_like_helper\src"

python .\wechat_pc_moments_image_test.py
```

运行前需要保证：

1. 电脑微信已经登录；
2. 微信窗口不要最小化；
3. 朋友圈入口模板已经校准；
4. 电脑不要锁屏；
5. 微信窗口最好不要被其他窗口完全遮挡。

### 5. 当前点赞规则

当前配置为：

```text
每轮点赞数量：30 个
每轮结束后休息：30 分钟
```

对应代码配置：

```python
ROUND_LIKE_COUNT = 30
ROUND_REST_SECONDS = 30 * 60
```

如果要修改每轮点赞数量，可以改：

```python
ROUND_LIKE_COUNT = 30
```

如果要修改休息时间，可以改：

```python
ROUND_REST_SECONDS = 30 * 60
```

例如改成休息 10 分钟：

```python
ROUND_REST_SECONDS = 10 * 60
```

### 6. 后台点击与后台滚动

当前已经改成后台点击和后台滚动，主要依赖：

```python
win32api
win32con
win32gui
```

安装依赖：

```powershell
python -m pip install pywin32
```

相比 `pyautogui`，现在的好处是：

```text
不移动真实鼠标
不影响正常鼠标操作
点击和滚动直接发送到微信窗口
```

但仍然需要注意：

```text
微信窗口不能最小化
电脑不能锁屏
窗口最好保持可见
```

### 7. 自动跳过已点赞朋友圈

当前会点开“··”菜单后检查左侧按钮状态：

```text
如果菜单显示“赞” → 点击
如果菜单显示“取消” → 跳过
```

这样可以避免：

```text
已经点过赞
↓
再次点击
↓
变成取消赞
```

当前判断逻辑是通过截图检测菜单中红色/粉色爱心像素数量。

### 8. 本轮去重机制

程序会记录本轮已经处理过的朋友圈。

作用：

```text
防止同一条朋友圈因为滚动位置变化，被重复识别
防止第二次点击同一条，导致取消赞
```

注意：

```text
这是本轮去重，不是历史去重
每一轮开始时会清空本轮记录
```

### 9. 自动定时运行

如果需要每天固定时间运行，可以使用任务计划程序。

目标：

```text
每天 08:00 启动脚本
每天 00:00 停止脚本
```

#### 新建启动脚本

文件路径：

```text
E:\wechat_like_helper\src\start_wechat_like.bat
```

内容：

```bat
@echo off
chcp 65001 >nul

cd /d "E:\wechat_like_helper\src"

if not exist "logs" mkdir logs

echo ========================================
echo 启动微信朋友圈点赞脚本
echo 时间：%date% %time%
echo ========================================

python "wechat_pc_moments_image_test.py" >> "logs\wechat_like_%date:~0,4%%date:~5,2%%date:~8,2%.log" 2>&1
```

#### 新建停止脚本

文件路径：

```text
E:\wechat_like_helper\src\stop_wechat_like.bat
```

内容：

```bat
@echo off
chcp 65001 >nul

echo ========================================
echo 停止微信朋友圈点赞脚本
echo 时间：%date% %time%
echo ========================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"$ps = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*wechat_pc_moments_image_test.py*' }; if ($ps) { $ps | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host '已停止微信点赞脚本' } else { Write-Host '没有找到正在运行的微信点赞脚本' }"

exit
```

#### 创建每天 08:00 启动任务

管理员 PowerShell 执行：

```powershell
schtasks /Create /TN "WechatMomentsLike_Start" /TR "E:\wechat_like_helper\src\start_wechat_like.bat" /SC DAILY /ST 08:00 /F
```

#### 创建每天 00:00 停止任务

管理员 PowerShell 执行：

```powershell
schtasks /Create /TN "WechatMomentsLike_Stop" /TR "E:\wechat_like_helper\src\stop_wechat_like.bat" /SC DAILY /ST 00:00 /F
```

### 10. 任务计划程序设置

打开任务计划程序：

```text
Win + S
搜索：任务计划程序
```

找到：

```text
WechatMomentsLike_Start
WechatMomentsLike_Stop
```

建议设置：

```text
只在用户登录时运行
使用最高权限运行
```

不要选择：

```text
不管用户是否登录都要运行
```

因为脚本需要操作电脑微信窗口，必须有桌面环境。

### 11. 手动测试

启动测试：

```powershell
cd "E:\wechat_like_helper\src"

.\start_wechat_like.bat
```

停止测试：

```powershell
cd "E:\wechat_like_helper\src"

.\stop_wechat_like.bat
```

### 12. 注意事项

1. 电脑微信必须保持登录；
2. 微信窗口不能最小化；
3. 电脑不要锁屏；
4. 如果识别位置不准，查看 `debug_pc_wechat` 里的调试截图；
5. 如果误判已点赞，可以调整 `menu_is_cancel_like()` 里的红色像素阈值；
6. 如果滚动跳过帖子，可以调小滚动格数；
7. 如果同一条朋友圈重复出现，本轮去重会跳过，避免取消赞；
8. 如果需要重新校准朋友圈入口，可以删除：

```powershell
Remove-Item "E:\wechat_like_helper\src\debug_pc_wechat\moments_icon_template.png" -Force
```

然后重新运行脚本，按提示选择朋友圈图标。

## 十、4个微信同时运行点赞

### 1. 前提条件

1. 电脑已登录4个微信窗口（用微信多开助手或其他方式打开）；
2. 4个微信窗口不能最小化；
3. 电脑不要锁屏；
4. 每个微信的朋友圈图标模板已校准（第一次运行时会提示选择）。

### 2. 启动4个微信点赞

打开 PowerShell，执行：

```powershell
cd "E:\wechat_like_helper\src"

python launch_4_wechat.py
```

脚本会：

1. 自动检测电脑上所有屏幕和微信窗口；
2. 根据屏幕大小自动计算不重叠的窗口位置；
3. 启动4个独立worker进程，每个绑定一个微信；
4. 每个worker独立点赞，互不干扰；
5. 自动重启异常退出的worker。

### 3. 自动位置计算

脚本会自动检测所有屏幕，按面积从大到小分配窗口：

- 每个账号需要：主窗口(900x650) + 朋友圈窗口(576x558)
- 组合宽度约1496像素，高度约650像素
- 竖屏优先上下排列，横屏优先左右排列
- 所有位置自动计算，无需手动配置

### 4. 查看屏幕信息

```powershell
python launch_4_wechat.py --screens
```

### 5. 只列出计算位置，不启动

```powershell
python launch_4_wechat.py --list
```

### 6. 停止所有4个worker

```powershell
python launch_4_wechat.py --stop
```

### 6. 使用bat脚本启动/停止

启动：

```powershell
.\start_4_wechat.bat
```

停止：

```powershell
.\stop_4_wechat.bat
```

### 7. 日志位置

```text
E:\wechat_like_helper\logs\wechat_4x\worker_A_YYYYMMDD.log
E:\wechat_like_helper\logs\wechat_4x\worker_B_YYYYMMDD.log
E:\wechat_like_helper\logs\wechat_4x\worker_C_YYYYMMDD.log
E:\wechat_like_helper\logs\wechat_4x\worker_D_YYYYMMDD.log
```

### 8. 注意事项

1. 必须先打开4个微信窗口，再运行脚本；
2. 每个微信窗口第一次运行时会提示校准朋友圈图标（选择编号），之后会自动记住；
3. 脚本默认08:00-00:00运行，可在代码中修改`RUN_START_HOUR`和`RUN_END_HOUR`；
4. 每个worker每轮点赞30个，休息30分钟，可在`wechat_pc_moments_image_test.py`中修改`ROUND_LIKE_COUNT`和`ROUND_REST_SECONDS`；
5. 如果某个微信窗口关闭或退出，对应worker会自动重启（1小时内最多5次）。
