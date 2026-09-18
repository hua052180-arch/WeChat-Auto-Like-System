# -*- coding: utf-8 -*-
"""
自动生成的 worker 文件。
账号：A

不要手动改这个文件。
真正逻辑仍然来自：
E:\wechat_like_helper\src\wechat_pc_moments_image_test.py
"""

import os
import runpy
import sys
from pathlib import Path

ACCOUNT = "A"

WORKDIR = Path(r"E:\wechat_like_helper\src")
ORIGINAL_SCRIPT = Path(r"E:\wechat_like_helper\src\wechat_pc_moments_image_test.py")

os.environ["WECHAT_MULTI_ACCOUNT"] = ACCOUNT
os.environ["WECHAT_MULTI_WORKER"] = "1"

os.chdir(str(WORKDIR))

if str(WORKDIR) not in sys.path:
    sys.path.insert(0, str(WORKDIR))

print("=" * 80)
print(f"微信朋友圈点赞 worker 启动，账号={ACCOUNT}")
print(f"原始脚本：{ORIGINAL_SCRIPT}")
print("=" * 80)

runpy.run_path(str(ORIGINAL_SCRIPT), run_name="__main__")
