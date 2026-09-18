# -*- coding: utf-8 -*-
"""
微信朋友圈点赞：按 PID 绑定微信窗口的通用 Worker

作用：
1. 不修改原来的 wechat_pc_moments_image_test.py
2. 启动时传入 --pid，只操作指定 PID 的微信窗口
3. 支持指定主微信窗口位置
4. 支持指定朋友圈窗口位置
5. 适合主机多开微信时，一个 PID 一个点赞任务

示例：

右侧竖屏微信：
python wechat_like_worker_by_pid.py --pid 47764 --main 1960 -760 900 650 --moments 2260 -80 576 558

左屏微信：
python wechat_like_worker_by_pid.py --pid 74672 --main -1850 80 900 650 --moments -1250 180 576 558
"""

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path

import psutil
import win32con
import win32gui
import win32process
import cv2
import mss
import numpy as np
try:
    import pytesseract
except Exception:
    pytesseract = None
from pywinauto import Desktop


WORKDIR = Path(__file__).resolve().parent
ORIGINAL_SCRIPT = WORKDIR / "wechat_pc_moments_image_test.py"


TARGET_PID = None
TARGET_HWND = None

MAIN_WINDOW_X = 0
MAIN_WINDOW_Y = 0
MAIN_WINDOW_WIDTH = 1080
MAIN_WINDOW_HEIGHT = 780

MOMENTS_WINDOW_X = 0
MOMENTS_WINDOW_Y = 0
MOMENTS_WINDOW_WIDTH = 576
MOMENTS_WINDOW_HEIGHT = 558

MOMENTS_WINDOW_TOPMOST = False
MOMENTS_TITLE_TEMPLATE = WORKDIR / "debug_pc_wechat" / "moments_title_template.png"


def _window_has_moments_title_template(window) -> bool:
    """不依赖 OCR，直接匹配朋友圈页面标题栏截图模板。"""
    try:
        template = cv2.imread(str(MOMENTS_TITLE_TEMPLATE), cv2.IMREAD_COLOR)
        if template is None:
            return False
        rect = window.rectangle()
        left, top = int(rect.left), int(rect.top)
        width, height = int(rect.width()), int(rect.height())
        if width < 300 or height < 150:
            return False
        crop_height = max(100, min(220, int(height * 0.28)))
        with mss.mss() as sct:
            shot = np.array(sct.grab({"left": left, "top": top, "width": width, "height": crop_height}))
        image = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        best = 0.0
        for scale in (0.75, 0.85, 1.0, 1.15, 1.3):
            resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
            if resized.shape[0] > image.shape[0] or resized.shape[1] > image.shape[1]:
                continue
            score = float(cv2.matchTemplate(image, resized, cv2.TM_CCOEFF_NORMED).max())
            best = max(best, score)
        if best >= 0.72:
            print(f"[朋友圈标题模板] 识别成功：分数={best:.3f}")
            return True
    except Exception as exc:
        print(f"[朋友圈标题模板] 识别失败：{exc}")
    return False


def _screen_window_has_moments_title(window) -> bool:
    """用朋友圈窗口截图顶部的可见标题做兜底识别。"""
    if _window_has_moments_title_template(window):
        return True
    if pytesseract is None:
        return False
    try:
        rect = window.rectangle()
        left, top = int(rect.left), int(rect.top)
        width, height = int(rect.width()), int(rect.height())
        if width < 300 or height < 300:
            return False

        crop_height = max(90, min(180, int(height * 0.24)))
        monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": crop_height,
        }
        with mss.mss() as sct:
            shot = np.array(sct.grab(monitor))
        image = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
        enlarged = cv2.resize(image, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
        text = pytesseract.image_to_string(
            gray,
            lang="chi_sim+eng",
            config="--psm 11",
        )
        matched = "朋友圈" in text or "朋友圈" in text.replace(" ", "")
        if matched:
            cv2.imwrite(str(WORKDIR / "debug_pc_wechat" / "moments_title_ocr.png"), image)
            print(f"[朋友圈标题OCR] 识别成功：{text.strip()!r}")
        return matched
    except Exception as exc:
        print(f"[朋友圈标题OCR] 识别失败：{exc}")
        return False


def is_target_pid(pid: int) -> bool:
    return int(pid) == int(TARGET_PID)


def is_wechat_process(pid: int) -> bool:
    try:
        name = psutil.Process(pid).name().lower()
        return name in ["weixin.exe", "wechat.exe"]
    except Exception:
        return False


def get_hwnd_pid(hwnd: int) -> int:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid)
    except Exception:
        return -1


def move_hwnd(
    hwnd: int,
    x: int,
    y: int,
    width: int,
    height: int,
    topmost: bool = False,
) -> None:
    """
    恢复窗口，并移动到指定位置。
    可以把 -32000 最小化窗口恢复出来。
    """
    insert_after = (
        win32con.HWND_TOPMOST
        if topmost
        else win32con.HWND_NOTOPMOST
    )

    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.4)

        win32gui.SetWindowPos(
            hwnd,
            insert_after,
            int(x),
            int(y),
            int(width),
            int(height),
            win32con.SWP_SHOWWINDOW,
        )

        time.sleep(0.6)

    except Exception as exc:
        print(f"[移动窗口失败] HWND={hwnd}，原因：{exc}")


def find_window_wrapper_by_hwnd(hwnd: int):
    desktop = Desktop(backend="uia")

    for window in desktop.windows():
        try:
            if int(window.handle) == int(hwnd):
                return window
        except Exception:
            continue

    return None


def list_target_pid_windows():
    """
    列出目标 PID 下所有窗口。
    用于调试。
    """
    result = []

    def callback(hwnd, _):
        try:
            pid = get_hwnd_pid(hwnd)

            if not is_target_pid(pid):
                return

            title = win32gui.GetWindowText(hwnd)
            visible = win32gui.IsWindowVisible(hwnd)
            rect = win32gui.GetWindowRect(hwnd)

            result.append(
                {
                    "hwnd": hwnd,
                    "pid": pid,
                    "title": title,
                    "visible": visible,
                    "rect": rect,
                }
            )

        except Exception:
            pass

    win32gui.EnumWindows(callback, None)

    return result


def find_target_main_wechat_window():
    """
    只找指定 PID 下的微信主窗口。
    """
    if not psutil.pid_exists(int(TARGET_PID)):
        raise RuntimeError(
            f"指定 PID 不存在：{TARGET_PID}"
        )

    if not is_wechat_process(int(TARGET_PID)):
        raise RuntimeError(
            f"指定 PID 不是微信进程：{TARGET_PID}"
        )

    windows = list_target_pid_windows()

    print("=" * 80)
    print(f"[PID绑定] 正在查找微信主窗口，PID={TARGET_PID}")
    print("=" * 80)

    candidates = []

    for item in windows:
        hwnd = item["hwnd"]
        if TARGET_HWND is not None and int(hwnd) != int(TARGET_HWND):
            continue
        title = item["title"]
        visible = item["visible"]
        left, top, right, bottom = item["rect"]

        width = right - left
        height = bottom - top

        print(
            f"[PID窗口] HWND={hwnd} | "
            f"VISIBLE={visible} | "
            f"TITLE={repr(title)} | "
            f"RECT=({left}, {top}, {right}, {bottom}) | "
            f"SIZE={width}x{height}"
        )

        score = 0

        if title == "微信":
            score += 10

        if visible:
            score += 3

        # 正常主窗口
        if width >= 700 and height >= 500:
            score += 5

        # 最小化窗口可能是 -32000 坐标，宽高很小，但标题仍然是微信
        if left <= -30000 and top <= -30000 and title == "微信":
            score += 4

        if score <= 0:
            continue

        candidates.append(
            {
                "hwnd": hwnd,
                "title": title,
                "visible": visible,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "score": score,
            }
        )

    if not candidates:
        raise RuntimeError(
            f"PID={TARGET_PID} 下没有找到可用的微信主窗口。"
        )

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    best = candidates[0]

    print(
        "[PID绑定] 选中微信主窗口："
        f"PID={TARGET_PID} | "
        f"HWND={best['hwnd']} | "
        f"title={best['title']} | "
        f"left={best['left']} | "
        f"top={best['top']} | "
        f"width={best['width']} | "
        f"height={best['height']} | "
        f"score={best['score']}"
    )

    move_hwnd(
        best["hwnd"],
        MAIN_WINDOW_X,
        MAIN_WINDOW_Y,
        MAIN_WINDOW_WIDTH,
        MAIN_WINDOW_HEIGHT,
        topmost=False,
    )

    window = find_window_wrapper_by_hwnd(best["hwnd"])

    if window is None:
        raise RuntimeError(
            f"已经找到 HWND={best['hwnd']}，但 pywinauto 无法绑定窗口。"
        )

    try:
        window.set_focus()
    except Exception:
        pass

    time.sleep(0.8)

    return window


def activate_wechat_by_pid():
    """
    替换原脚本里的 activate_wechat。
    """
    window = find_target_main_wechat_window()

    try:
        window.set_focus()
    except Exception:
        pass

    time.sleep(0.8)

    try:
        rect = window.rectangle()

        print("[PID绑定] 已激活指定微信窗口")
        print(
            f"窗口：left={rect.left}, top={rect.top}, "
            f"width={rect.width()}, height={rect.height()}"
        )

    except Exception:
        pass

    return window


def wait_for_moments_popup_window_by_pid(main_window, timeout: float = 15.0):
    """
    替换原脚本里的 wait_for_moments_popup_window。
    只找指定 PID 下弹出的朋友圈窗口。
    """
    start_time = time.time()

    try:
        main_hwnd = int(main_window.handle)
    except Exception:
        main_hwnd = None
    try:
        main_rect = main_window.rectangle()
    except Exception:
        main_rect = None

    while time.time() - start_time < timeout:
        # 新版微信把朋友圈作为主窗口右侧扩展区域，仍使用同一个 HWND。
        # 此时不能排除主窗口；宽度明显变大即代表最右侧朋友圈面板已打开。
        try:
            expanded_rect = main_window.rectangle()
            expanded_width = int(expanded_rect.width())
            expanded_height = int(expanded_rect.height())
            if (
                expanded_width >= int(MAIN_WINDOW_WIDTH * 1.20)
                and expanded_height >= int(MAIN_WINDOW_HEIGHT * 0.75)
            ):
                print(
                    "[朋友圈窗口] 检测到微信主窗口已展开右侧朋友圈面板："
                    f"left={expanded_rect.left} | top={expanded_rect.top} | "
                    f"width={expanded_width} | height={expanded_height}"
                )
                try:
                    main_window.set_focus()
                except Exception:
                    pass
                return main_window
        except Exception:
            pass

        desktop = Desktop(backend="uia")

        candidates = []

        for window in desktop.windows():
            try:
                hwnd = int(window.handle)

                if main_hwnd is not None and hwnd == main_hwnd:
                    continue

                pid = window.element_info.process_id
                if not window.is_visible():
                    continue

                title = window.window_text()
                is_moments_title = (
                    "朋友圈" in title
                    or "moments" in title.lower()
                )

                rect = window.rectangle()
                width = rect.width()
                height = rect.height()

                # 第一条链路的约定：朋友圈是微信在主窗口右侧打开的扩展窗口。
                # 先用位置和尺寸锁定扩展窗口，不依赖其内部内容是否变化。
                is_right_wechat_extension = False
                if main_rect is not None:
                    try:
                        process_name = psutil.Process(pid).name().lower()
                    except Exception:
                        process_name = ""
                    is_right_wechat_extension = (
                        rect.left >= main_rect.right - 120
                        and width >= 300
                        and height >= 300
                        and ("wechat" in process_name or "weixin" in process_name)
                    )

                # 新版微信的朋友圈独立页可能属于另一个 PID。
                # 标题栏明确是“朋友圈”时，允许跨 PID 绑定；
                # 其它窗口仍然必须属于目标微信 PID。
                if (
                    not is_moments_title
                    and not is_right_wechat_extension
                    and not is_target_pid(pid)
                ):
                    # 有些版本把“朋友圈”作为窗口内部绘制的标题，
                    # UIA/Win32 读不到标题，只能对候选窗口顶部截图 OCR。
                    is_moments_title = _screen_window_has_moments_title(window)
                    if not is_moments_title:
                        continue

                if width < 300 or height < 300:
                    continue

                score = 0

                if is_moments_title:
                    score += 10
                if is_right_wechat_extension:
                    score += 8

                # 朋友圈弹窗一般宽度不会太大
                if 420 <= width <= 760:
                    score += 3

                if 450 <= height <= 900:
                    score += 3

                # 距离目标朋友圈位置越近越好
                distance = (
                    abs(rect.left - MOMENTS_WINDOW_X)
                    + abs(rect.top - MOMENTS_WINDOW_Y)
                )

                candidates.append(
                    {
                        "window": window,
                        "hwnd": hwnd,
                        "pid": pid,
                        "title": title,
                        "left": rect.left,
                        "top": rect.top,
                        "width": width,
                        "height": height,
                        "score": score,
                        "distance": distance,
                    }
                )

            except Exception:
                continue

        if candidates:
            candidates.sort(
                key=lambda item: (
                    item["score"],
                    -item["distance"],
                ),
                reverse=True,
            )

            best = candidates[0]

            print(
                "[PID绑定] 已找到朋友圈弹窗："
                f"PID={best['pid']} | "
                f"HWND={best['hwnd']} | "
                f"title={best['title']} | "
                f"left={best['left']} | "
                f"top={best['top']} | "
                f"width={best['width']} | "
                f"height={best['height']} | "
                f"score={best['score']}"
            )

            move_hwnd(
                best["hwnd"],
                MOMENTS_WINDOW_X,
                MOMENTS_WINDOW_Y,
                MOMENTS_WINDOW_WIDTH,
                MOMENTS_WINDOW_HEIGHT,
                topmost=MOMENTS_WINDOW_TOPMOST,
            )

            try:
                best["window"].set_focus()
            except Exception:
                pass

            time.sleep(0.8)

            return best["window"]

        print(
            f"[PID绑定] 暂未找到 PID={TARGET_PID} 的朋友圈弹窗，继续等待..."
        )

        time.sleep(0.5)

    raise RuntimeError(
        f"没有找到 PID={TARGET_PID} 的朋友圈独立窗口。"
        "请确认该微信窗口确实打开了朋友圈。"
    )


def load_original_module():
    if not ORIGINAL_SCRIPT.exists():
        raise FileNotFoundError(
            f"找不到原脚本：{ORIGINAL_SCRIPT}"
        )

    spec = importlib.util.spec_from_file_location(
        "wechat_original_by_pid",
        str(ORIGINAL_SCRIPT),
    )

    module = importlib.util.module_from_spec(spec)

    sys.modules["wechat_original_by_pid"] = module

    spec.loader.exec_module(module)

    return module


def parse_args():
    parser = argparse.ArgumentParser(
        description="按 HWND/PID 绑定微信窗口运行朋友圈点赞"
    )

    parser.add_argument(
        "--pid",
        type=int,
        required=True,
        help="目标微信进程 PID",
    )

    parser.add_argument(
        "--hwnd",
        type=int,
        default=None,
        help="目标微信主窗口 HWND；多开共用 PID 时用于区分窗口",
    )

    parser.add_argument(
        "--main",
        nargs=4,
        type=int,
        required=True,
        metavar=("X", "Y", "W", "H"),
        help="主微信窗口位置，例如：--main -1850 80 900 650",
    )

    parser.add_argument(
        "--moments",
        nargs=4,
        type=int,
        required=True,
        metavar=("X", "Y", "W", "H"),
        help="朋友圈窗口位置，例如：--moments -1250 180 576 558",
    )

    parser.add_argument(
        "--no-topmost",
        action="store_true",
        help="朋友圈窗口不置顶",
    )

    return parser.parse_args()


def main():
    global TARGET_PID
    global TARGET_HWND

    global MAIN_WINDOW_X
    global MAIN_WINDOW_Y
    global MAIN_WINDOW_WIDTH
    global MAIN_WINDOW_HEIGHT

    global MOMENTS_WINDOW_X
    global MOMENTS_WINDOW_Y
    global MOMENTS_WINDOW_WIDTH
    global MOMENTS_WINDOW_HEIGHT

    global MOMENTS_WINDOW_TOPMOST

    args = parse_args()

    TARGET_PID = args.pid
    TARGET_HWND = args.hwnd

    (
        MAIN_WINDOW_X,
        MAIN_WINDOW_Y,
        MAIN_WINDOW_WIDTH,
        MAIN_WINDOW_HEIGHT,
    ) = args.main

    (
        MOMENTS_WINDOW_X,
        MOMENTS_WINDOW_Y,
        MOMENTS_WINDOW_WIDTH,
        MOMENTS_WINDOW_HEIGHT,
    ) = args.moments

    # 默认不置顶；保留 --no-topmost 参数兼容旧启动脚本。
    MOMENTS_WINDOW_TOPMOST = False

    os.chdir(str(WORKDIR))

    if str(WORKDIR) not in sys.path:
        sys.path.insert(0, str(WORKDIR))

    print("=" * 80)
    print("微信朋友圈点赞 HWND/PID 绑定 Worker 启动")
    print(f"目标 PID：{TARGET_PID}")
    print(f"目标 HWND：{TARGET_HWND}")
    print(
        f"主窗口位置：{MAIN_WINDOW_X}, {MAIN_WINDOW_Y}, "
        f"{MAIN_WINDOW_WIDTH}, {MAIN_WINDOW_HEIGHT}"
    )
    print(
        f"朋友圈位置：{MOMENTS_WINDOW_X}, {MOMENTS_WINDOW_Y}, "
        f"{MOMENTS_WINDOW_WIDTH}, {MOMENTS_WINDOW_HEIGHT}"
    )
    print(f"朋友圈置顶：{MOMENTS_WINDOW_TOPMOST}")
    print("=" * 80)

    module = load_original_module()

    # 给原脚本补充固定窗口参数
    module.MAIN_WINDOW_X = MAIN_WINDOW_X
    module.MAIN_WINDOW_Y = MAIN_WINDOW_Y
    module.MAIN_WINDOW_WIDTH = MAIN_WINDOW_WIDTH
    module.MAIN_WINDOW_HEIGHT = MAIN_WINDOW_HEIGHT

    module.MOMENTS_WINDOW_X = MOMENTS_WINDOW_X
    module.MOMENTS_WINDOW_Y = MOMENTS_WINDOW_Y
    module.MOMENTS_WINDOW_WIDTH = MOMENTS_WINDOW_WIDTH
    module.MOMENTS_WINDOW_HEIGHT = MOMENTS_WINDOW_HEIGHT
    module.MOMENTS_WINDOW_TOPMOST = MOMENTS_WINDOW_TOPMOST

    # 关键：替换原脚本找微信窗口的逻辑
    module.activate_wechat = activate_wechat_by_pid
    module.wait_for_moments_popup_window = wait_for_moments_popup_window_by_pid

    # 防止原脚本解析当前 worker 的参数
    sys.argv = [str(ORIGINAL_SCRIPT)]

    module.main()


if __name__ == "__main__":
    main()
