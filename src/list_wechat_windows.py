# -*- coding: utf-8 -*-
"""
检测当前电脑上打开的微信窗口

输出：
1. 窗口序号
2. 标题
3. PID
4. HWND 句柄
5. 窗口位置
6. 所在屏幕
"""

import time
import psutil
from pywinauto import Desktop


SCREENS = [
    {
        "name": "左屏 DISPLAY3",
        "x1": -1920,
        "y1": 0,
        "x2": -1,
        "y2": 1080,
    },
    {
        "name": "主屏 DISPLAY1",
        "x1": 0,
        "y1": 0,
        "x2": 1920,
        "y2": 1080,
    },
    {
        "name": "右侧竖屏 DISPLAY2",
        "x1": 1920,
        "y1": -828,
        "x2": 3120,
        "y2": 1092,
    },
]


def get_screen_name(left: int, top: int) -> str:
    for screen in SCREENS:
        if (
            screen["x1"] <= left <= screen["x2"]
            and screen["y1"] <= top <= screen["y2"]
        ):
            return screen["name"]

    return "未知屏幕 / 可能在屏幕外"


def main():
    desktop = Desktop(backend="uia")

    print("=" * 90)
    print("当前检测到的微信窗口")
    print("=" * 90)

    results = []

    for window in desktop.windows():
        try:
            title = window.window_text()
            pid = window.element_info.process_id
            process_name = psutil.Process(pid).name()

            if process_name.lower() not in [
                "weixin.exe",
                "wechat.exe",
            ]:
                continue

            if not window.is_visible():
                continue

            rect = window.rectangle()

            width = rect.width()
            height = rect.height()

            if width < 100 or height < 100:
                continue

            screen_name = get_screen_name(
                rect.left,
                rect.top,
            )

            results.append(
                {
                    "title": title,
                    "pid": pid,
                    "process": process_name,
                    "hwnd": int(window.handle),
                    "left": rect.left,
                    "top": rect.top,
                    "width": width,
                    "height": height,
                    "screen": screen_name,
                }
            )

        except Exception:
            continue

    results.sort(
        key=lambda item: (
            item["screen"],
            item["left"],
            item["top"],
        )
    )

    if not results:
        print("没有检测到微信窗口。")
        return

    for index, item in enumerate(results, start=1):
        print(f"[{index}]")
        print(f"标题：{item['title']}")
        print(f"进程：{item['process']}")
        print(f"PID：{item['pid']}")
        print(f"HWND：{item['hwnd']}")
        print(f"屏幕：{item['screen']}")
        print(
            f"位置：left={item['left']}, "
            f"top={item['top']}, "
            f"width={item['width']}, "
            f"height={item['height']}"
        )
        print("-" * 90)

    print("=" * 90)
    print(f"共检测到 {len(results)} 个微信窗口")
    print("=" * 90)

    time.sleep(3)


if __name__ == "__main__":
    main()