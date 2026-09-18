# -*- coding: utf-8 -*-
from pathlib import Path

path = Path(r"E:\wechat_like_helper\src\wechat_like_worker_by_pid.py")
text = path.read_text(encoding="utf-8")

if "capture_wechat_window_safe_by_pid" in text:
    print("已经打过补丁，不需要重复处理。")
    raise SystemExit

# 1. 补充 import
import_block = """import random

import cv2
import mss
import numpy as np
from PIL import ImageGrab
"""

if "import time\n" in text:
    text = text.replace("import time\n", "import time\n" + import_block, 1)
else:
    raise RuntimeError("没有找到 import time，无法自动插入 import。")

# 2. 插入安全截图函数
safe_func = r'''
def capture_wechat_window_safe_by_pid(window):
    """
    安全截图函数。

    原脚本使用 mss 截图时，双微信并发可能偶发 BitBlt 拒绝访问。
    这里增加：
    1. 窗口恢复
    2. mss 多次重试
    3. PIL ImageGrab 兜底
    """
    hwnd = int(window.handle)

    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass

    time.sleep(random.uniform(0.05, 0.25))

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)

    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        raise RuntimeError(
            f"截图失败：窗口尺寸异常 hwnd={hwnd}, "
            f"rect=({left},{top},{right},{bottom})"
        )

    if left <= -30000 or top <= -30000:
        raise RuntimeError(
            f"截图失败：窗口可能被最小化 hwnd={hwnd}, "
            f"rect=({left},{top},{right},{bottom})"
        )

    monitor = {
        "left": int(left),
        "top": int(top),
        "width": int(width),
        "height": int(height),
    }

    last_error = None

    # 第一方案：mss，多试几次
    for attempt in range(1, 4):
        try:
            with mss.mss() as sct:
                shot = sct.grab(monitor)

            image = np.array(shot)

            if image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

            print(
                f"[安全截图] mss 成功："
                f"hwnd={hwnd}, left={left}, top={top}, "
                f"width={width}, height={height}"
            )

            return image

        except Exception as exc:
            last_error = exc
            print(
                f"[安全截图] mss 第 {attempt}/3 次失败：{exc}"
            )
            time.sleep(0.4 + attempt * 0.2)

    # 第二方案：PIL ImageGrab 兜底
    try:
        pil_image = ImageGrab.grab(
            bbox=(left, top, right, bottom),
            all_screens=True,
        )

        image = np.array(pil_image)

        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        print(
            f"[安全截图] PIL ImageGrab 兜底成功："
            f"hwnd={hwnd}, left={left}, top={top}, "
            f"width={width}, height={height}"
        )

        return image

    except Exception as exc:
        raise RuntimeError(
            f"截图彻底失败。mss错误={last_error}，PIL错误={exc}"
        )
'''

marker = "def load_original_module():"
if marker not in text:
    raise RuntimeError("没有找到 def load_original_module()，无法插入安全截图函数。")

text = text.replace(marker, safe_func + "\n\n" + marker, 1)

# 3. 在 main 里替换原脚本截图函数
old_line = "    module.wait_for_moments_popup_window = wait_for_moments_popup_window_by_pid\n"
new_line = old_line + "    module.capture_wechat_window = capture_wechat_window_safe_by_pid\n"

if old_line not in text:
    raise RuntimeError("没有找到 wait_for_moments_popup_window 替换位置。")

text = text.replace(old_line, new_line, 1)

path.write_text(text, encoding="utf-8")

print("补丁完成：已给 wechat_like_worker_by_pid.py 增加安全截图兜底。")
