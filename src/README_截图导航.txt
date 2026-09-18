微信截图导航测试
================

文件：
01_enter_moments_screenshot.py

放置位置：
E:\wechat_like_helper\src

第一次运行前，双击：
安装截图导航依赖.bat

运行：
cd "E:\wechat_like_helper\src"
python .\01_enter_moments_screenshot.py

功能：
1. 自动识别USB安卓真机，排除MuMu模拟器；
2. 打开微信；
3. 截图判断当前界面；
4. 如果在聊天详情等二级页面，每次只返回一次，然后重新截图判断；
5. 截图识别微信底部四栏导航；
6. 截图定位第三栏“发现”；
7. 点击后重新截图，验证是否到达“发现”页；
8. 截图定位发现页最上方彩色入口“朋友圈”；
9. 点击后重新截图，验证是否进入朋友圈。

本文件暂时不点赞。

调试截图均保存在：
E:\wechat_like_helper\screenshots

主要截图：
- step_home_check_1_raw.png
- step_home_check_1_classified.png
- home_discover_target.png
- step_after_discover_raw.png
- discover_moments_target.png
- step_after_moments_raw.png
- step_after_moments_classified.png
