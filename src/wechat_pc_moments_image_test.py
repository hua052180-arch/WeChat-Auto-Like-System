from __future__ import annotations


from __future__ import annotations

import ctypes
import time
from pathlib import Path
import win32api
import win32con
import win32gui

# 解决 Windows 缩放导致的截图坐标偏移
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import cv2
import mss
import numpy as np
import psutil
import pyautogui
from pywinauto import Desktop


DEBUG_DIR = Path("debug_pc_wechat")
DEBUG_DIR.mkdir(exist_ok=True)

# 你说左侧第四个是朋友圈
MOMENTS_ICON_INDEX = 4

MOMENTS_TEMPLATE_PATH = DEBUG_DIR / "moments_icon_template.png"

# 第一条链路使用的两个入口模板：
# 图一在主窗口左侧 10%，图二在点击图一后的页面右侧 50%。
CHAIN1_LEFT_TEMPLATE_PATH = DEBUG_DIR / "chain1_left_template.png"
CHAIN1_RIGHT_TEMPLATE_PATH = DEBUG_DIR / "chain1_right_template.png"
CHAIN1_MOMENTS_TITLE_TEMPLATE_PATH = DEBUG_DIR / "chain1_moments_title_template.png"
CHAIN1_LEFT_THRESHOLD = 0.52
CHAIN1_RIGHT_THRESHOLD = 0.52
CHAIN1_MOMENTS_TITLE_THRESHOLD = 0.62
LIKE_MENU_TEMPLATE_PATH = DEBUG_DIR / "like_menu_template.png"

# 微信新版界面需要先进入“发现”，再双击“朋友圈”条目。
# 该坐标是发现页内容区内的相对坐标（按当前窗口比例缩放），
# 不再把侧栏的“发现”图标误认为是朋友圈独立窗口入口。
DISCOVER_MOMENTS_POINT = (128, 155)
DISCOVER_REFERENCE_SIZE = (877, 639)

PROCESSED_POST_HASHES = []

ROUND_LIKE_COUNT = 30

# 每轮点赞完成后，休息 30 分钟
ROUND_REST_SECONDS = 60 * 60

# 固定电脑微信主窗口位置：右侧竖屏 DISPLAY2
MAIN_WINDOW_X = 1960
MAIN_WINDOW_Y = -760
MAIN_WINDOW_WIDTH = 1080
MAIN_WINDOW_HEIGHT = 780

# 固定朋友圈窗口位置：右侧竖屏 DISPLAY2
MOMENTS_WINDOW_X = 2260
MOMENTS_WINDOW_Y = -80
MOMENTS_WINDOW_WIDTH = 576
MOMENTS_WINDOW_HEIGHT = 558

# 是否让朋友圈窗口置顶
MOMENTS_WINDOW_TOPMOST = False


class _FixedRegion:
    def __init__(self, left: int, top: int, width: int, height: int):
        self.left = int(left)
        self.top = int(top)
        self.right = self.left + int(width)
        self.bottom = self.top + int(height)

    def width(self) -> int:
        return self.right - self.left

    def height(self) -> int:
        return self.bottom - self.top


class _MomentsRegionWindow:
    """把展开微信窗口的最右侧朋友圈面板作为旧代码的窗口对象。"""
    def __init__(self, base_window):
        self._base_window = base_window
        self._panel_offset = int(MAIN_WINDOW_WIDTH)
        rect = base_window.rectangle()
        # 展开后的右侧面板与微信主窗口共用 HWND。按实际窗口尺寸取
        # 面板的完整高度，避免原来的 558 像素截图截不到帖子按钮。
        panel_left = int(rect.left) + int(MAIN_WINDOW_WIDTH)
        panel_width = int(rect.right) - panel_left
        if panel_width < 300:
            raise RuntimeError("朋友圈右侧面板宽度不足，不能开始点赞")
        self._fixed_region = _FixedRegion(
            panel_left,
            int(rect.top),
            panel_width,
            int(rect.height()),
        )
        print(
            "[第一链路] 朋友圈实际截图区域："
            f"left={panel_left}, top={rect.top}, "
            f"width={panel_width}, height={rect.height()}"
        )

    @property
    def handle(self):
        return self._base_window.handle

    def rectangle(self):
        # 微信移动后，截图和滚轮跟随实际面板位置，不能沿用启动时坐标。
        left, top, right, bottom = win32gui.GetWindowRect(int(self.handle))
        panel_left = left + self._panel_offset
        if right - panel_left < 300 or bottom - top < 300:
            raise RuntimeError("朋友圈面板已收起或窗口不可见，停止操作")
        self._fixed_region = _FixedRegion(panel_left, top, right - panel_left, bottom - top)
        return self._fixed_region

    def set_focus(self):
        result = self._base_window.set_focus()
        try:
            hwnd = int(self._base_window.handle)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE
                | win32con.SWP_NOSIZE
                | win32con.SWP_SHOWWINDOW,
            )
        except Exception:
            pass
        return result

LIKED_HISTORY_PATH = DEBUG_DIR / "liked_post_history.txt"

def find_wechat_window():
    """
    通过进程名查找真正的电脑微信窗口。

    不再用标题里的 WeChat 匹配，
    避免把 VS Code 项目 WECHAT_LIKE_HELPER 误识别成微信。
    """
    desktop = Desktop(backend="uia")

    candidates = []

    for window in desktop.windows():
        try:
            title = window.window_text()
            pid = window.element_info.process_id

            process_name = psutil.Process(pid).name()

            print(
                f"[窗口扫描] title={title} | "
                f"process={process_name}"
            )

            process_name_lower = process_name.lower()

            is_wechat_process = (
                "wechat" in process_name_lower
                or "weixin" in process_name_lower
            )

            # 排除 VS Code
            is_not_vscode = (
                "code" not in process_name_lower
                and "visual studio code" not in title.lower()
            )

            if is_wechat_process and is_not_vscode:
                rect = window.rectangle()

                candidates.append(
                    {
                        "window": window,
                        "title": title,
                        "process": process_name,
                        "left": rect.left,
                        "top": rect.top,
                        "width": rect.width(),
                        "height": rect.height(),
                        "area": rect.width() * rect.height(),
                    }
                )

        except Exception:
            continue

    if not candidates:
        raise RuntimeError(
            "没有找到真正的电脑微信窗口。"
            "请确认电脑微信已经打开，并且不是最小化。"
        )

    candidates.sort(
        key=lambda item: item["area"],
        reverse=True,
    )

    best = candidates[0]

    print(
        "[微信窗口] 已找到："
        f"title={best['title']} | "
        f"process={best['process']} | "
        f"left={best['left']} | "
        f"top={best['top']} | "
        f"width={best['width']} | "
        f"height={best['height']}"
    )

    return best["window"]

def move_window_to_fixed_position(
    window,
    x: int,
    y: int,
    width: int,
    height: int,
    topmost: bool = False,
) -> None:
    """
    把窗口固定到指定位置和大小。
    """
    try:
        hwnd = int(window.handle)

        print(
            f"[固定窗口] 移动窗口到："
            f"x={x}, y={y}, width={width}, height={height}, "
            f"topmost={topmost}"
        )

        insert_after = (
            win32con.HWND_TOPMOST
            if topmost
            else win32con.HWND_NOTOPMOST
        )

        win32gui.SetWindowPos(
            hwnd,
            insert_after,
            int(x),
            int(y),
            int(width),
            int(height),
            win32con.SWP_SHOWWINDOW,
        )

        time.sleep(0.5)

    except Exception as exc:
        print(f"[固定窗口] 失败：{exc}")
def activate_wechat():
    window = find_wechat_window()

    try:
        window.set_focus()
    except Exception:
        pass

    time.sleep(0.8)

    # 三屏环境下，不再判断 rect.left > 1800 是屏幕外
    # 直接固定到右侧竖屏 DISPLAY2
    move_window_to_fixed_position(
        window,
        MAIN_WINDOW_X,
        MAIN_WINDOW_Y,
        MAIN_WINDOW_WIDTH,
        MAIN_WINDOW_HEIGHT,
        topmost=False,
    )

    time.sleep(0.8)

    try:
        window.set_focus()
    except Exception:
        pass

    rect = window.rectangle()

    print(f"已激活微信窗口：{window.window_text()}")
    print(
        f"窗口：left={rect.left}, top={rect.top}, "
        f"width={rect.width()}, height={rect.height()}"
    )

    return window

def capture_wechat_window(window):
    """
    使用 mss 截取真正的电脑微信窗口。
    """
    rect = window.rectangle()

    left = int(rect.left)
    top = int(rect.top)
    width = int(rect.width())
    height = int(rect.height())

    print(
        f"[截图] 准备截取窗口区域："
        f"left={left}, top={top}, width={width}, height={height}"
    )

    with mss.mss() as sct:
        monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }

        shot = sct.grab(monitor)

        image = np.array(shot)

        image = cv2.cvtColor(
            image,
            cv2.COLOR_BGRA2BGR,
        )

    mean_value = float(np.mean(image))
    std_value = float(np.std(image))

    print(
        f"[截图检查] 平均亮度={mean_value:.2f}，"
        f"对比度={std_value:.2f}"
    )

    debug_raw_path = DEBUG_DIR / "raw_wechat_capture.png"

    cv2.imwrite(
        str(debug_raw_path),
        image,
    )

    print(
        f"[截图] 原始窗口截图已保存："
        f"{debug_raw_path.resolve()}"
    )

    return image

def wait_for_moments_popup_window(main_window, timeout: float = 10.0):
    """
    点击朋友圈入口后，等待并找到新弹出的朋友圈独立窗口。

    朋友圈不是原来的微信主窗口，所以后续截图、滚动都要用这个窗口。
    """
    start_time = time.time()

    try:
        main_handle = int(main_window.handle)
    except Exception:
        main_handle = None

    try:
        main_rect = main_window.rectangle()
        main_pid = main_window.element_info.process_id
    except Exception:
        main_rect = None
        main_pid = None

    while time.time() - start_time < timeout:
        # 新版微信把朋友圈作为主窗口右侧扩展区域，仍使用同一个 HWND。
        # 主窗口宽度明显扩展时，直接将当前窗口交给旧点赞逻辑。
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
                handle = int(window.handle)

                if main_handle is not None and handle == main_handle:
                    continue

                if not window.is_visible():
                    continue

                pid = window.element_info.process_id
                title = window.window_text()

                # 新版微信可能把朋友圈独立页放到另一个 PID 下。
                # 窗口标题栏明确显示“朋友圈”时，以标题为最高优先级，
                # 不再强制要求它和微信主窗口使用同一个 PID。
                is_moments_title = (
                    "朋友圈" in title
                    or "moments" in title.lower()
                )

                if not is_moments_title:
                    if main_pid is not None and pid != main_pid:
                        continue

                    process_name = psutil.Process(pid).name().lower()

                    if (
                        "wechat" not in process_name
                        and "weixin" not in process_name
                    ):
                        continue
                else:
                    try:
                        process_name = psutil.Process(pid).name().lower()
                    except Exception:
                        process_name = "unknown"

                rect = window.rectangle()

                width = rect.width()
                height = rect.height()
                area = width * height

                # 过滤太小的提示窗口
                if width < 350 or height < 350:
                    continue

                # 过滤和主窗口完全一样的窗口
                if main_rect is not None:
                    same_as_main = (
                        abs(rect.left - main_rect.left) < 5
                        and abs(rect.top - main_rect.top) < 5
                        and abs(width - main_rect.width()) < 5
                        and abs(height - main_rect.height()) < 5
                    )

                    if same_as_main:
                        continue

                candidates.append(
                    {
                        "window": window,
                        "title": title,
                        "process": process_name,
                        "left": rect.left,
                        "top": rect.top,
                        "width": width,
                        "height": height,
                        "area": area,
                    }
                )

            except Exception:
                continue

        if candidates:
            candidates.sort(
                key=lambda item: item["area"],
                reverse=True,
            )

            best = candidates[0]

            print(
                "[朋友圈窗口] 已找到独立窗口："
                f"title={best['title']} | "
                f"process={best['process']} | "
                f"left={best['left']} | "
                f"top={best['top']} | "
                f"width={best['width']} | "
                f"height={best['height']}"
            )

            moments_window = best["window"]

            move_window_to_fixed_position(
                moments_window,
                MOMENTS_WINDOW_X,
                MOMENTS_WINDOW_Y,
                MOMENTS_WINDOW_WIDTH,
                MOMENTS_WINDOW_HEIGHT,
                topmost=MOMENTS_WINDOW_TOPMOST,
            )

            try:
                moments_window.set_focus()
            except Exception:
                pass

            time.sleep(0.8)

            return moments_window

        time.sleep(0.5)

    raise RuntimeError(
        "没有找到朋友圈独立窗口。"
        "请确认点击朋友圈后是否真的弹出了新窗口。"
    )

def close_moments_window(window) -> None:
    """
    关闭朋友圈独立窗口。
    """
    if isinstance(window, _MomentsRegionWindow):
        # 第一链路的朋友圈面板与微信主窗口共用 HWND，不能向它发送 WM_CLOSE。
        print("[退出朋友圈] 右侧面板与微信主窗口共用窗口，保留微信主窗口。")
        return
    try:
        hwnd = int(window.handle)

        print(
            f"[退出朋友圈] 正在关闭朋友圈窗口：hwnd={hwnd}"
        )

        win32gui.PostMessage(
            hwnd,
            win32con.WM_CLOSE,
            0,
            0,
        )

        time.sleep(1.0)

    except Exception as exc:
        print(
            f"[退出朋友圈] 关闭窗口失败：{exc}"
        )

def sleep_with_countdown(seconds: int) -> None:
    """
    休息倒计时。
    按 Ctrl+C 可以手动停止程序。
    """
    remaining = int(seconds)

    while remaining > 0:
        minutes = remaining // 60
        secs = remaining % 60

        print(
            f"\r[休息中] 距离下一轮还有 "
            f"{minutes:02d}:{secs:02d}",
            end="",
            flush=True,
        )

        time.sleep(1)
        remaining -= 1

    print()

def scroll_moments_panel(window, wheel_amount: int, repeat: int) -> None:
    """第一链路：后台向朋友圈面板投递滚轮消息，不移动用户鼠标。"""
    if wheel_amount == 0 or repeat <= 0:
        return
    rect = window.rectangle()
    # 用户图二是面板下部的帖子区，其下半部分位于整块面板的底部。
    # 选横向 70%、纵向 92%，避开顶部封面、标题栏和底边框。
    screen_x = int(rect.left + rect.width() * 0.70)
    screen_y = int(rect.top + rect.height() * 0.92)
    point = (screen_x, screen_y)
    hwnd = int(window.handle)

    hit = win32gui.WindowFromPoint(point)
    target_hwnd = int(hit or hwnd)
    if win32gui.GetAncestor(target_hwnd, win32con.GA_ROOT) != hwnd:
        raise RuntimeError("朋友圈滚动点被其它窗口遮挡，未发送后台滚轮")

    direction = -1 if wheel_amount < 0 else 1
    # 第一条链路固定每次滚动 1.5 个后台滚轮单位，逐步识别避免跳过朋友圈。
    half_page_units = 1.5
    total_units = direction * half_page_units * int(repeat)
    lparam = win32api.MAKELONG(int(screen_x), int(screen_y))
    wparam = ((int(round(total_units * 120)) << 16) & 0xFFFFFFFF)
    print(
        f"[帖子区滚动] 后台滚轮，落点=({screen_x}, {screen_y})，"
        f"约滚动页面50%，滚轮量={total_units}，目标HWND={target_hwnd}"
    )
    win32gui.PostMessage(
        target_hwnd,
        win32con.WM_MOUSEWHEEL,
        wparam,
        lparam,
    )
    time.sleep(0.25)


def scroll_target_window(
    window,
    wheel_amount: int = -1,
    repeat: int = 1,
) -> None:
    """
    两条链路都向目标窗口发送后台滚轮消息，不移动真实鼠标。

    修复：
    不再使用 window.rectangle()，
    避免 pywinauto 偶发 COMError。
    """
    if isinstance(window, _MomentsRegionWindow):
        scroll_moments_panel(window, wheel_amount, repeat)
        return
    try:
        hwnd = int(window.handle)

        fixed = getattr(window, "_fixed_region", None)
        if fixed is not None:
            left, top, right, bottom = (
                fixed.left,
                fixed.top,
                fixed.right,
                fixed.bottom,
            )
        else:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)

        width = right - left
        height = bottom - top

        if fixed is not None:
            # 在朋友圈面板右侧 40%、下方 20% 的交集内滚动。
            screen_x = left + int(width * 0.80)
            screen_y = top + int(height * 0.88)
        else:
            screen_x = left + width - 18
            screen_y = top + int(height * 0.55)

        target_hwnd = hwnd
        if fixed is not None:
            candidate_hwnd = win32gui.WindowFromPoint((screen_x, screen_y))
            if candidate_hwnd and (
                candidate_hwnd == hwnd
                or win32gui.IsChild(hwnd, candidate_hwnd)
            ):
                target_hwnd = candidate_hwnd

        print(
            f"[后台慢滚] 发送滚轮到窗口右侧安全区："
            f"({screen_x}, {screen_y})，"
            f"滚动格数={wheel_amount}，次数={repeat}，目标HWND={target_hwnd}"
        )

        lparam = win32api.MAKELONG(
            int(screen_x),
            int(screen_y),
        )

        direction = -1 if wheel_amount < 0 else 1
        one_delta = direction * 120

        wparam = (one_delta << 16) & 0xFFFFFFFF

        # 第二条链路保持旧版滚动节奏：按原来的滚轮格数逐条发送。
        step_count = max(1, abs(int(wheel_amount)))
        total_steps = step_count * max(1, int(repeat))
        print(
            f"[后台慢滚] 恢复旧版滚动节奏，滚动格数={step_count}，"
            f"次数={repeat}，总消息数={total_steps}"
        )
        for _ in range(total_steps):
            win32gui.PostMessage(
                target_hwnd,
                win32con.WM_MOUSEWHEEL,
                wparam,
                lparam,
            )
            time.sleep(0.16)

    except Exception as exc:
        print(
            f"[后台滚动失败] {exc}"
        )
        raise

def find_more_button_with_scroll(
    window,
    max_scrolls: int = 80,
) -> str:
    """
    慢速查找朋友圈右侧两个点按钮。

    返回：
    liked：成功点赞
    already_liked：历史已点赞，需要退出
    not_found：找不到
    """
    unchanged_scrolls = 0
    for index in range(1, max_scrolls + 1):
        print(
            f"[更多按钮检测] 第 {index} 次检测"
        )

        result = open_more_menu_once(window)

        if result == "liked":
            return "liked"

        if result == "already_liked":
            return "already_liked"

        if result == "duplicate":
            print(
                "[更多按钮检测] 本轮重复朋友圈，"
                "小幅滚动跳过。"
            )

            scroll_target_window(
                window,
                wheel_amount=-2,
                repeat=1,
            )

            time.sleep(0.8 if isinstance(window, _MomentsRegionWindow) else 0.5)

            continue

        if result == "uncertain":
            # 点赞后未复核到“取消”时，停留在当前朋友圈重试，不能直接滚走。
            print("[点赞复核] 当前状态未确认，停留当前朋友圈重新识别。")
            time.sleep(0.6)
            continue

        print(
            "[更多按钮检测] 当前屏幕没找到两个点，"
            "慢速向下滚动继续找。"
        )

        # 每次滚动 20 格，随后重新截图确认页面确实移动。
        before_scroll = capture_wechat_window(window)
        scroll_target_window(
            window,
            wheel_amount=-1,
            repeat=1,
        )

        # 第一条链路后台滚轮位移较大，给微信更多时间完成渲染再识别。
        time.sleep(0.85 if isinstance(window, _MomentsRegionWindow) else 0.55)
        after_scroll = capture_wechat_window(window)
        body_start = int(before_scroll.shape[0] * 0.20)
        body_end = int(before_scroll.shape[0] * 0.95)
        before_body = before_scroll[body_start:body_end]
        after_body = after_scroll[body_start:body_end]
        change = float(cv2.absdiff(before_body, after_body).mean())
        print(f"[滚动验证] 前后截图平均变化={change:.2f}")
        if change < 0.8:
            unchanged_scrolls += 1
            if unchanged_scrolls >= 3:
                print("[滚动失败] 连续3次截图未变化，停止重复扫描同一画面。")
                return "not_found"
        else:
            unchanged_scrolls = 0

    print(
        "连续多次没有找到两个点按钮。"
    )

    return "not_found"

def find_more_button_by_dots(
    image: np.ndarray,
) -> tuple[int, int] | None:
    """
    识别朋友圈右侧的“..”更多按钮。
    """
    height, width = image.shape[:2]

    # 只扫描截图最右侧 50%，覆盖不同微信版本中位置变化的“...”按钮。
    # 后续仍用候选中心点的右侧安全区校验，避免误识别正文元素。
    roi_x1 = int(width * 0.50)

    # 扫描标题栏以下的整个帖子区域；滚动后按钮可能出现在上半部。
    roi_y1 = int(height * 0.18)

    roi_x2 = int(width * 0.98)
    roi_y2 = int(height * 0.96)

    roi = image[
        roi_y1:roi_y2,
        roi_x1:roi_x2,
    ]

    hsv = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2HSV,
    )

    h_channel, s_channel, v_channel = cv2.split(hsv)

    bg_mask = (
        (v_channel >= 25)
        & (v_channel <= 160)
        & (s_channel <= 110)
    ).astype(np.uint8) * 255

    bg_mask = cv2.morphologyEx(
        bg_mask,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), np.uint8),
    )

    contours, _ = cv2.findContours(
        bg_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    candidates: list[tuple[int, int, int, int, float]] = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        area = w * h

        if area < 180 or area > 3200:
            continue

        if w < 18 or w > 90:
            continue

        if h < 12 or h > 55:
            continue

        ratio = w / max(h, 1)

        if ratio < 0.8 or ratio > 5.0:
            continue

        abs_x = roi_x1 + x
        abs_y = roi_y1 + y

        center_x_tmp = abs_x + w // 2
        center_y_tmp = abs_y + h // 2

        # 必须非常靠右
        if center_x_tmp < int(width * 0.86):
            continue

        # 排除标题栏里的菜单按钮。
        if center_y_tmp < int(height * 0.18):
            continue

        candidate_crop = roi[
            y:y + h,
            x:x + w,
        ]

        candidate_gray = cv2.cvtColor(
            candidate_crop,
            cv2.COLOR_BGR2GRAY,
        )

        dot_mask = (
            candidate_gray >= 85
        ).astype(np.uint8) * 255

        dot_mask = cv2.morphologyEx(
            dot_mask,
            cv2.MORPH_OPEN,
            np.ones((2, 2), np.uint8),
        )

        dot_contours, _ = cv2.findContours(
            dot_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        dots: list[tuple[int, int]] = []

        for dot_contour in dot_contours:
            dx, dy, dw, dh = cv2.boundingRect(dot_contour)
            dot_area = cv2.contourArea(dot_contour)

            if dot_area < 1 or dot_area > 80:
                continue

            if dw < 2 or dw > 12:
                continue

            if dh < 2 or dh > 12:
                continue

            dots.append(
                (
                    dx + dw // 2,
                    dy + dh // 2,
                )
            )

        has_two_dots = False

        for i in range(len(dots)):
            for j in range(i + 1, len(dots)):
                p1 = dots[i]
                p2 = dots[j]

                y_span = abs(p1[1] - p2[1])
                x_gap = abs(p1[0] - p2[0])

                if y_span > 10:
                    continue

                if not (3 <= x_gap <= 28):
                    continue

                has_two_dots = True
                break

            if has_two_dots:
                break

        if not has_two_dots:
            continue

        score = abs_x + (height - abs_y) * 0.2

        candidates.append(
            (
                abs_x,
                abs_y,
                w,
                h,
                score,
            )
        )

    debug = image.copy()

    for index, (x, y, w, h, score) in enumerate(
        candidates,
        start=1,
    ):
        cv2.rectangle(
            debug,
            (x, y),
            (x + w, y + h),
            (0, 0, 255),
            2,
        )

        cv2.putText(
            debug,
            str(index),
            (x, y - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

    debug_path = DEBUG_DIR / "more_button_detected.png"

    cv2.imwrite(
        str(debug_path),
        debug,
    )

    print(
        f"[更多按钮] 调试图已保存："
        f"{debug_path.resolve()}"
    )

    if not candidates:
        print("[更多按钮] 没有识别到真正的两个点按钮。")
        return None

    candidates.sort(
        key=lambda item: item[1]
    )

    x, y, w, h, score = candidates[0]

    center_x = int(x + w / 2)
    center_y = int(y + h / 2)

    if center_x < int(width * 0.78):
        print(
            f"[更多按钮] 候选位置不在右侧安全区，跳过："
            f"({center_x}, {center_y})"
        )
        return None

    print(
        f"[更多按钮] 识别到两个点按钮："
        f"({center_x}, {center_y})，"
        f"按钮区域=({x}, {y}, {w}, {h})"
    )

    return center_x, center_y

def close_open_menu(window) -> None:
    """
    关闭已经打开的朋友圈点赞菜单。
    """
    try:
        hwnd = int(window.handle)

        win32gui.PostMessage(
            hwnd,
            win32con.WM_KEYDOWN,
            win32con.VK_ESCAPE,
            0,
        )

        time.sleep(0.05)

        win32gui.PostMessage(
            hwnd,
            win32con.WM_KEYUP,
            win32con.VK_ESCAPE,
            0,
        )

        time.sleep(0.2)

    except Exception as exc:
        print(f"[菜单关闭] 失败：{exc}")


def menu_is_cancel_like(
    menu_image: np.ndarray,
    more_x: int,
    more_y: int,
) -> bool:
    """
    判断打开菜单后，左侧按钮是不是“取消”。

    已点赞状态下，电脑微信菜单左侧一般会出现红色/粉色爱心，
    而未点赞状态一般是白色爱心 + 赞。
    """
    height, width = menu_image.shape[:2]

    # 新版菜单中，“赞/取消”位于菜单最左侧；不要依赖固定宽度，
    # 否则窗口缩放后容易把检测框偏到“评论”区域。
    x1 = max(0, more_x - 360)
    x2 = min(width, more_x + 20)

    y1 = max(
        0,
        more_y - 28,
    )

    y2 = min(
        height,
        more_y + 28,
    )

    crop = menu_image[
        y1:y2,
        x1:x2,
    ]

    debug = menu_image.copy()

    cv2.rectangle(
        debug,
        (x1, y1),
        (x2, y2),
        (0, 255, 255),
        2,
    )

    debug_path = DEBUG_DIR / "cancel_like_check.png"

    cv2.imwrite(
        str(debug_path),
        debug,
    )

    if crop.size == 0:
        return False

    hsv = cv2.cvtColor(
        crop,
        cv2.COLOR_BGR2HSV,
    )

    h_channel, s_channel, v_channel = cv2.split(hsv)

    # 红色/粉色爱心区域
    red_mask = (
        (
            (h_channel <= 12)
            | (h_channel >= 165)
        )
        & (s_channel >= 70)
        & (v_channel >= 90)
    )

    red_count = int(
        np.count_nonzero(red_mask)
    )

    print(
        f"[取消检测] 红色像素数量={red_count}，"
        f"调试图={debug_path.resolve()}"
    )

    # “取消”菜单有红色爱心；“赞”菜单没有红色元素。
    # 使用较低阈值并扩大检测框，兼容 DPI 缩放和抗锯齿。
    if red_count >= 20:
        print(
            "[菜单文字判断] 检测到红色爱心，对应“取消”，不点击。"
        )
        return True

    print("[菜单文字判断] 未检测到红色爱心，按“赞”处理。")

    return False

def open_more_menu_once(window) -> str:
    """
    找到当前页面的两个点按钮，点击后点赞。

    逻辑：
    1. 找两个点；
    2. 点开菜单；
    3. 如果菜单是“取消”，说明已经点赞过，跳过；
    4. 如果菜单是“赞”，直接点击赞。
    """
    image = capture_wechat_window(window)

    point = find_more_button_by_dots(image)

    if point is None:
        return "not_found"

    more_x, more_y = point

    post_hash = make_post_hash(
        image,
        more_x,
        more_y,
    )

    # 本轮已经处理过的，直接跳过
    # 但注意：这里只在点开菜单前判断一次
    if post_already_processed(post_hash):
        print(
            "[帖子去重] 当前朋友圈本轮已经处理过，跳过。"
        )
        return "duplicate"

    # 第一步：点击两个点
    click_window_point(
        window,
        more_x,
        more_y,
    )

    time.sleep(0.8)

    menu_image = capture_wechat_window(window)

    menu_path = DEBUG_DIR / "after_click_more_menu.png"

    cv2.imwrite(
        str(menu_path),
        menu_image,
    )

    print(
        f"已点击两个点按钮，菜单截图已保存："
        f"{menu_path.resolve()}"
    )

    # 第二步：判断是不是已经点赞过
    if menu_is_cancel_like(
        menu_image,
        more_x,
        more_y,
    ):
        print(
            "[点赞状态] 当前朋友圈已经点赞过，"
            "不取消，直接跳过。"
        )

        mark_post_processed(
            post_hash
        )

        close_open_menu(
            window
        )

        return "duplicate"

    # 第三步：菜单不是“取消”，点击菜单左侧第一个按钮“赞”。
    # 菜单动画或截图时序不稳定时，连续重拍，不能因为一次识别失败就直接滚动。
    like_point = None
    for retry_index in range(3):
        like_point = find_like_button_in_open_menu(menu_image)
        if like_point is not None:
            if retry_index:
                print(f"[赞按钮] 第 {retry_index + 1} 次截图识别成功。")
            break
        if retry_index < 2:
            print(
                f"[赞按钮] 第 {retry_index + 1} 次未确认菜单，"
                "等待后重新截图，不滚动。"
            )
            time.sleep(0.35)
            menu_image = capture_wechat_window(window)
    if like_point is None:
        # 新版微信菜单横条有时无法被灰色轮廓识别，但菜单本身已经打开。
        # 以“三个点”为锚点，左侧约 0.36 个面板宽度处就是“赞”文字中心；
        # 点击后仍必须通过“取消”复核，避免误点时计数。
        fallback_offset = max(150, int(menu_image.shape[1] * 0.36))
        like_point = (
            max(0, int(more_x - fallback_offset)),
            int(more_y),
        )
        print(
            f"[赞按钮] 菜单轮廓识别失败，使用三个点锚定备用坐标："
            f"{like_point}，点击后复核“取消”。"
        )

    like_x, like_y = like_point

    debug = menu_image.copy()

    cv2.circle(
        debug,
        (more_x, more_y),
        10,
        (0, 0, 255),
        2,
    )

    cv2.putText(
        debug,
        "more",
        (more_x - 45, more_y - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 0, 255),
        2,
        cv2.LINE_AA,
    )

    cv2.circle(
        debug,
        (like_x, like_y),
        12,
        (0, 255, 0),
        -1,
    )

    cv2.putText(
        debug,
        "like",
        (like_x - 35, like_y - 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )

    like_debug_path = DEBUG_DIR / "like_button_relative_detected.png"

    cv2.imwrite(
        str(like_debug_path),
        debug,
    )

    print(
        f"[赞按钮] 菜单不是取消，准备点击赞："
        f"({like_x}, {like_y})"
    )

    print(
        f"[赞按钮] 调试图已保存："
        f"{like_debug_path.resolve()}"
    )

    click_window_point(
        window,
        like_x,
        like_y,
    )

    time.sleep(1.2)

    # 点击后重新打开菜单复核：只有看到“取消”才确认点赞成功。
    click_window_point(window, more_x, more_y)
    time.sleep(0.8)
    verify_image = capture_wechat_window(window)
    verify_path = DEBUG_DIR / "after_click_like_verify.png"
    cv2.imwrite(str(verify_path), verify_image)
    verified = menu_is_cancel_like(verify_image, more_x, more_y)
    close_open_menu(window)
    if not verified:
        print("[点赞复核] 点击后未看到“取消”，本条不计入成功，稍后重试。")
        return "uncertain"

    print("[点赞复核] 已确认菜单变为“取消”，点赞成功。")
    mark_post_processed(post_hash)
    return "liked"


def find_like_button_in_open_menu(
    image: np.ndarray,
) -> tuple[int, int] | None:
    """
    在已经弹出的“...”菜单中识别“赞”按钮。

    逻辑：
    1. 找朋友圈窗口里的浅灰色横向菜单；
    2. 菜单左侧约 1/4 位置就是“赞”；
    3. 返回“赞”按钮中心点。
    """
    height, width = image.shape[:2]

    # 菜单通常出现在右侧、中下区域
    roi_x1 = int(width * 0.35)
    roi_y1 = int(height * 0.25)
    roi_x2 = int(width * 0.98)
    roi_y2 = int(height * 0.95)

    roi = image[
        roi_y1:roi_y2,
        roi_x1:roi_x2,
    ]

    gray = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2GRAY,
    )

    hsv = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2HSV,
    )

    h_channel, s_channel, v_channel = cv2.split(hsv)

    # 菜单是偏灰色，比背景亮
    mask = (
        (gray >= 65)
        & (gray <= 145)
        & (s_channel <= 60)
    ).astype(np.uint8) * 255

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        np.ones((9, 9), np.uint8),
    )

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    candidates: list[tuple[int, int, int, int]] = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        area = w * h

        if area < 2500:
            continue

        # 菜单是横向长条
        if w < 140 or w > int(width * 0.75):
            continue

        if h < 28 or h > 80:
            continue

        if w / max(h, 1) < 2.8:
            continue

        abs_x = roi_x1 + x
        abs_y = roi_y1 + y

        candidates.append(
            (
                abs_x,
                abs_y,
                w,
                h,
            )
        )

    debug = image.copy()

    for index, (x, y, w, h) in enumerate(
        candidates,
        start=1,
    ):
        cv2.rectangle(
            debug,
            (x, y),
            (x + w, y + h),
            (0, 0, 255),
            2,
        )

        cv2.putText(
            debug,
            str(index),
            (x, y - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

    if not candidates:
        debug_path = DEBUG_DIR / "like_menu_not_found.png"

        cv2.imwrite(
            str(debug_path),
            debug,
        )

        print(
            f"[赞按钮] 没有识别到点赞菜单，"
            f"调试图已保存：{debug_path.resolve()}"
        )

        return None

    # 选择最靠右、最像菜单的候选
    candidates.sort(
        key=lambda item: (
            item[1],
            -item[0],
        )
    )

    menu_x, menu_y, menu_w, menu_h = candidates[0]

    # “赞”在菜单左侧，大概是菜单宽度的 1/4 处
    like_x = int(menu_x + menu_w * 0.26)
    like_y = int(menu_y + menu_h * 0.50)

    cv2.circle(
        debug,
        (like_x, like_y),
        12,
        (0, 255, 0),
        -1,
    )

    debug_path = DEBUG_DIR / "like_button_detected.png"

    cv2.imwrite(
        str(debug_path),
        debug,
    )

    print(
        f"[赞按钮] 已识别到点赞菜单："
        f"menu=({menu_x}, {menu_y}, {menu_w}, {menu_h})"
    )

    print(
        f"[赞按钮] 预计点击“赞”坐标："
        f"({like_x}, {like_y})"
    )

    print(
        f"[赞按钮] 调试图已保存："
        f"{debug_path.resolve()}"
    )

    return like_x, like_y


def click_like_from_open_menu(window) -> bool:
    """
    在已经打开的“...”菜单里点击“赞”。
    点击前人工确认。
    """
    image = capture_wechat_window(window)

    point = find_like_button_in_open_menu(
        image
    )

    if point is None:
        return False

    like_x, like_y = point

    # confirm = input(
    #     f"识别到“赞”按钮：({like_x}, {like_y})。"
    #     f"确认点赞请输入 y："
    # ).strip().lower()

    # if confirm != "y":
    #     print("没有执行点赞。")
    #     return False

    click_window_point(
        window,
        like_x,
        like_y,
    )

    time.sleep(0.8)

    after_image = capture_wechat_window(window)

    after_path = DEBUG_DIR / "after_click_like.png"

    cv2.imwrite(
        str(after_path),
        after_image,
    )

    print(
        f"已点击“赞”，截图已保存："
        f"{after_path.resolve()}"
    )

    return True

def find_left_sidebar_icons(image: np.ndarray) -> list[tuple[int, int]]:
    """
    截图识别微信左侧栏图标。

    不使用固定坐标。
    逻辑：
    1. 截取窗口左侧工具栏区域；
    2. 找白色/绿色图标像素；
    3. 按纵向聚类；
    4. 得到每个图标中心点。
    """
    height, width = image.shape[:2]

    # 只分析左侧工具栏，不写死屏幕坐标
    sidebar_width = min(
        int(width * 0.08),
        90,
    )

    sidebar = image[
        0:height,
        0:sidebar_width,
    ]

    hsv = cv2.cvtColor(
        sidebar,
        cv2.COLOR_BGR2HSV,
    )

    gray = cv2.cvtColor(
        sidebar,
        cv2.COLOR_BGR2GRAY,
    )

    # 白色图标
    white_mask = cv2.inRange(
        gray,
        120,
        255,
    )

    # 微信绿色高亮图标
    green_mask = cv2.inRange(
        hsv,
        np.array([35, 50, 50], dtype=np.uint8),
        np.array([95, 255, 255], dtype=np.uint8),
    )

    mask = cv2.bitwise_or(
        white_mask,
        green_mask,
    )

    # 排除窗口顶部标题栏区域
    mask[0:int(height * 0.05), :] = 0

    # 形态学连接断开的图标线条
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        np.ones((5, 5), np.uint8),
    )

    # 按行统计亮像素，找到图标所在的纵向区域
    row_score = np.sum(mask > 0, axis=1)

    # 平滑
    kernel_size = 9
    kernel = np.ones(kernel_size) / kernel_size
    row_score_smooth = np.convolve(
        row_score,
        kernel,
        mode="same",
    )

    threshold = max(
        6,
        int(sidebar_width * 0.12),
    )

    active_rows = row_score_smooth > threshold

    groups: list[tuple[int, int]] = []
    start = None

    for y, active in enumerate(active_rows):
        if active and start is None:
            start = y

        if (
            not active
            or y == len(active_rows) - 1
        ) and start is not None:
            end = y

            if end - start >= 12:
                groups.append((start, end))

            start = None

    centers: list[tuple[int, int]] = []

    for y1, y2 in groups:
        group_mask = mask[y1:y2, :]

        ys, xs = np.where(group_mask > 0)

        if len(xs) == 0:
            continue

        center_x = int(np.mean(xs))
        center_y = int(y1 + np.mean(ys))

        # 排除太靠左/太靠右的误识别
        if not (
            int(sidebar_width * 0.15)
            <= center_x
            <= int(sidebar_width * 0.85)
        ):
            continue

        centers.append(
            (
                center_x,
                center_y,
            )
        )

    # 去重：相邻太近的合并
    cleaned: list[tuple[int, int]] = []

    for x, y in centers:
        if any(
            abs(y - old_y) < 28
            for _, old_y in cleaned
        ):
            continue

        cleaned.append((x, y))

    cleaned.sort(
        key=lambda point: point[1]
    )

    return cleaned


def draw_debug_image(
    image: np.ndarray,
    icons: list[tuple[int, int]],
) -> np.ndarray:
    debug = image.copy()

    for index, (x, y) in enumerate(
        icons,
        start=1,
    ):
        cv2.circle(
            debug,
            (x, y),
            16,
            (0, 0, 255),
            3,
        )

        cv2.putText(
            debug,
            str(index),
            (x + 22, y + 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )

    return debug

def click_window_point(window, x: int, y: int) -> None:
    """
    后台点击窗口内坐标，不移动真实鼠标。

    x, y 是截图里的窗口内坐标。
    """
    rect = window.rectangle()

    screen_x = rect.left + int(x)
    screen_y = rect.top + int(y)

    print(
        f"[后台点击] 窗口内=({x}, {y})，"
        f"屏幕=({screen_x}, {screen_y})"
    )

    try:
        hwnd = win32gui.WindowFromPoint(
            (
                int(screen_x),
                int(screen_y),
            )
        )

        if not hwnd:
            hwnd = int(window.handle)

        client_x, client_y = win32gui.ScreenToClient(
            hwnd,
            (
                int(screen_x),
                int(screen_y),
            )
        )

        lparam = win32api.MAKELONG(
            int(client_x),
            int(client_y),
        )

        win32gui.PostMessage(
            hwnd,
            win32con.WM_MOUSEMOVE,
            0,
            lparam,
        )

        time.sleep(0.05)

        win32gui.PostMessage(
            hwnd,
            win32con.WM_LBUTTONDOWN,
            win32con.MK_LBUTTON,
            lparam,
        )

        time.sleep(0.06)

        win32gui.PostMessage(
            hwnd,
            win32con.WM_LBUTTONUP,
            0,
            lparam,
        )

    except Exception as exc:
        print(
            f"[后台点击失败] {exc}"
        )
        raise


def double_click_window_point(window, x: int, y: int, interval: float = 0.04) -> None:
    """发送真正的 Windows 双击消息，避免两次普通点击间隔过长。"""
    rect = window.rectangle()
    screen_x = rect.left + int(x)
    screen_y = rect.top + int(y)

    print(f"[后台双击] 窗口内=({x}, {y})，间隔={interval:.2f}s")

    try:
        hwnd = win32gui.WindowFromPoint((int(screen_x), int(screen_y)))
        if not hwnd:
            hwnd = int(window.handle)

        client_x, client_y = win32gui.ScreenToClient(
            hwnd, (int(screen_x), int(screen_y))
        )
        lparam = win32api.MAKELONG(int(client_x), int(client_y))

        # 第一击：普通按下/抬起；第二击：系统双击消息。
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
        time.sleep(interval)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDBLCLK, win32con.MK_LBUTTON, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    except Exception as exc:
        print(f"[后台双击失败] {exc}")
        raise

def calc_image_hash(image: np.ndarray) -> np.ndarray:
    """
    计算图片感知哈希，用来判断是不是同一条朋友圈。
    """
    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    small = cv2.resize(
        gray,
        (16, 16),
        interpolation=cv2.INTER_AREA,
    )

    median = np.median(small)

    return small > median


def hash_distance(hash_a: np.ndarray, hash_b: np.ndarray) -> int:
    return int(
        np.count_nonzero(hash_a != hash_b)
    )


def make_post_hash(
    image: np.ndarray,
    more_x: int,
    more_y: int,
) -> np.ndarray:
    """
    根据两个点按钮附近区域生成帖子指纹。

    同一条朋友圈上下滑动后位置会变化，
    但以两个点按钮为锚点裁剪，内容相对稳定。
    """
    height, width = image.shape[:2]

    x1 = int(width * 0.05)
    x2 = int(width * 0.96)

    y1 = max(
        0,
        more_y - 320,
    )

    y2 = min(
        height,
        more_y + 70,
    )

    crop = image[
        y1:y2,
        x1:x2,
    ]

    debug_path = DEBUG_DIR / "current_post_hash_crop.png"

    cv2.imwrite(
        str(debug_path),
        crop,
    )

    return calc_image_hash(crop)


def post_already_processed(
    post_hash: np.ndarray,
    threshold: int = 22,
) -> bool:
    """
    判断当前朋友圈是否本轮已经处理过。
    """
    for old_hash in PROCESSED_POST_HASHES:
        distance = hash_distance(
            post_hash,
            old_hash,
        )

        if distance <= threshold:
            print(
                f"[帖子去重] 命中已处理朋友圈，"
                f"哈希距离={distance}，跳过。"
            )
            return True

    return False


def mark_post_processed(
    post_hash: np.ndarray,
) -> None:
    PROCESSED_POST_HASHES.append(
        post_hash
    )

    print(
        f"[帖子去重] 已记录当前朋友圈，"
        f"本轮已处理数量={len(PROCESSED_POST_HASHES)}"
    )

def hash_to_text(post_hash: np.ndarray) -> str:
    """
    把 16x16 的 bool hash 转成字符串，方便保存到文件。
    """
    flat = post_hash.astype(np.uint8).flatten()

    return "".join(
        "1" if value else "0"
        for value in flat
    )


def text_to_hash(text: str) -> np.ndarray:
    values = [
        char == "1"
        for char in text.strip()
        if char in {"0", "1"}
    ]

    arr = np.array(
        values,
        dtype=bool,
    )

    return arr.reshape(
        (16, 16)
    )


def load_liked_history() -> list[np.ndarray]:
    """
    读取历史已经点赞过的朋友圈指纹。
    """
    if not LIKED_HISTORY_PATH.exists():
        return []

    hashes: list[np.ndarray] = []

    with LIKED_HISTORY_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        for line in file:
            line = line.strip()

            if len(line) != 256:
                continue

            try:
                hashes.append(
                    text_to_hash(line)
                )
            except Exception:
                continue

    print(
        f"[历史点赞] 已读取历史记录数量：{len(hashes)}"
    )

    return hashes


def post_in_liked_history(
    post_hash: np.ndarray,
    threshold: int = 18,
) -> bool:
    """
    判断当前朋友圈是否历史已经点赞过。

    如果命中历史记录，说明已经滚到旧内容，
    按你的要求：退出朋友圈。
    """
    history_hashes = load_liked_history()

    for old_hash in history_hashes:
        distance = hash_distance(
            post_hash,
            old_hash,
        )

        if distance <= threshold:
            print(
                f"[历史点赞] 当前朋友圈历史已经点赞过，"
                f"哈希距离={distance}。"
            )

            return True

    return False


def save_liked_history(
    post_hash: np.ndarray,
) -> None:
    """
    保存本次成功点赞的朋友圈指纹。
    """
    LIKED_HISTORY_PATH.parent.mkdir(
        exist_ok=True
    )

    text = hash_to_text(
        post_hash
    )

    with LIKED_HISTORY_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(text + "\n")

    print(
        f"[历史点赞] 已写入历史记录："
        f"{LIKED_HISTORY_PATH.resolve()}"
    )

def preprocess_icon_for_match(image: np.ndarray) -> np.ndarray:
    """
    用边缘图匹配图标，降低颜色变化影响。
    """
    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.GaussianBlur(
        gray,
        (3, 3),
        0,
    )

    edges = cv2.Canny(
        gray,
        40,
        120,
    )

    return edges


def find_template_in_region(
    image: np.ndarray,
    template_path: Path,
    x1_ratio: float,
    x2_ratio: float,
    y1_ratio: float = 0.0,
    y2_ratio: float = 1.0,
    threshold: float = 0.52,
    debug_name: str = "template_region_match.png",
) -> tuple[int, int] | None:
    """在指定比例区域内做模板匹配，并返回窗口内中心坐标。"""
    template = cv2.imread(str(template_path))
    if template is None:
        print(f"[链路识别] 模板不存在：{template_path}")
        return None

    height, width = image.shape[:2]

    # 优先匹配用户提供的“赞|评论”菜单模板，避免新版菜单颜色变化
    # 导致原来的灰色轮廓检测漏掉有效菜单。
    menu_template = cv2.imread(str(LIKE_MENU_TEMPLATE_PATH), cv2.IMREAD_COLOR)
    if menu_template is not None:
        best_score = 0.0
        best_loc = None
        best_size = None
        for scale in (0.80, 0.90, 1.00, 1.10, 1.20):
            scaled = cv2.resize(
                menu_template,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_LINEAR,
            )
            if scaled.shape[0] > height or scaled.shape[1] > width:
                continue
            result = cv2.matchTemplate(image, scaled, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(result)
            if score > best_score:
                best_score = float(score)
                best_loc = loc
                best_size = scaled.shape[:2]

        if best_loc is not None and best_score >= 0.55:
            menu_x, menu_y = best_loc
            menu_w, menu_h = best_size[1], best_size[0]
            # 横条左侧第一个按钮的文字中心（“赞”），
            # 不能取 0.215 的爱心位置，否则可能点到图标边缘。
            like_x = int(menu_x + menu_w * 0.32)
            like_y = int(menu_y + menu_h * 0.52)
            print(
                f"[赞按钮] 模板识别到“赞”菜单："
                f"分数={best_score:.3f}，点击=({like_x}, {like_y})"
            )
            return like_x, like_y
    x1 = max(0, int(width * x1_ratio))
    x2 = min(width, int(width * x2_ratio))
    y1 = max(0, int(height * y1_ratio))
    y2 = min(height, int(height * y2_ratio))
    roi = image[y1:y2, x1:x2]

    roi_match = preprocess_icon_for_match(roi)
    template_match = preprocess_icon_for_match(template)
    th, tw = template_match.shape[:2]
    if th >= roi_match.shape[0] or tw >= roi_match.shape[1]:
        print(f"[链路识别] 模板大于识别区域：{template_path.name}")
        return None

    result = cv2.matchTemplate(roi_match, template_match, cv2.TM_CCOEFF_NORMED)
    _, score, _, max_loc = cv2.minMaxLoc(result)
    center_x = x1 + int(max_loc[0] + tw / 2)
    center_y = y1 + int(max_loc[1] + th / 2)

    debug = image.copy()
    cv2.rectangle(debug, (x1, y1), (x2, y2), (255, 180, 0), 2)
    if score >= threshold:
        cv2.rectangle(
            debug,
            (x1 + max_loc[0], y1 + max_loc[1]),
            (x1 + max_loc[0] + tw, y1 + max_loc[1] + th),
            (0, 255, 0),
            2,
        )
        cv2.circle(debug, (center_x, center_y), 8, (0, 255, 0), -1)

    cv2.imwrite(str(DEBUG_DIR / debug_name), debug)
    print(
        f"[链路识别] {template_path.name} 分数={score:.3f}，"
        f"阈值={threshold:.3f}，候选=({center_x}, {center_y})"
    )

    if score < threshold:
        return None
    return center_x, center_y


def try_first_moments_chain(window) -> bool:
    """第一条链路：识别左侧图一并单击，等待微信在右侧创建朋友圈扩展窗口。"""
    image = capture_wechat_window(window)
    first_point = find_template_in_region(
        image,
        CHAIN1_LEFT_TEMPLATE_PATH,
        0.0,
        0.10,
        debug_name="chain1_step1_left_match.png",
        threshold=CHAIN1_LEFT_THRESHOLD,
    )
    if first_point is None:
        print("[第一链路] 未识别到图一，转入第二链路。")
        return False

    print(f"[第一链路] 图一识别成功，单击：{first_point}")
    click_window_point(window, *first_point)
    time.sleep(2.0)
    after_first = capture_wechat_window(window)
    cv2.imwrite(str(DEBUG_DIR / "chain1_after_step1.png"), after_first)

    # 图一点击后，只在微信窗口右侧 50% 区域识别“朋友圈”标题。
    # 识别成功才允许进入旧的滑屏点赞循环。
    title_point = find_template_in_region(
        after_first,
        CHAIN1_MOMENTS_TITLE_TEMPLATE_PATH,
        0.50,
        1.0,
        debug_name="chain1_moments_title_match.png",
        threshold=CHAIN1_MOMENTS_TITLE_THRESHOLD,
    )
    if title_point is None:
        print("[第一链路] 右侧50%未识别到“朋友圈”标题，不启动点赞。")
        return False

    print(f"[第一链路] 右侧50%识别到“朋友圈”：{title_point}")
    return True


def save_moments_icon_template(
    image: np.ndarray,
    x: int,
    y: int,
    size: int = 46,
) -> None:
    """
    保存朋友圈图标模板。
    只保存原始截图里的小图，不保存红圈调试图。
    """
    height, width = image.shape[:2]

    half = size // 2

    x1 = max(0, x - half)
    y1 = max(0, y - half)
    x2 = min(width, x + half)
    y2 = min(height, y + half)

    template = image[
        y1:y2,
        x1:x2,
    ]

    cv2.imwrite(
        str(MOMENTS_TEMPLATE_PATH),
        template,
    )

    print(
        f"[模板] 已保存朋友圈图标模板："
        f"{MOMENTS_TEMPLATE_PATH.resolve()}"
    )


def find_moments_icon_by_template(
    image: np.ndarray,
) -> tuple[int, int] | None:
    """
    根据已保存的朋友圈图标模板，在左侧栏里查找朋友圈图标。
    """
    if not MOMENTS_TEMPLATE_PATH.exists():
        return None

    template = cv2.imread(
        str(MOMENTS_TEMPLATE_PATH)
    )

    if template is None:
        return None

    height, width = image.shape[:2]

    sidebar_width = min(
        int(width * 0.10),
        110,
    )

    sidebar = image[
        0:height,
        0:sidebar_width,
    ]

    sidebar_match = preprocess_icon_for_match(
        sidebar,
    )

    template_match = preprocess_icon_for_match(
        template,
    )

    th, tw = template_match.shape[:2]

    if (
        th >= sidebar_match.shape[0]
        or tw >= sidebar_match.shape[1]
    ):
        return None

    result = cv2.matchTemplate(
        sidebar_match,
        template_match,
        cv2.TM_CCOEFF_NORMED,
    )

    _, max_score, _, max_loc = cv2.minMaxLoc(
        result
    )

    print(
        f"[模板匹配] 朋友圈图标匹配分数："
        f"{max_score:.3f}"
    )

    if max_score < 0.28:
        print(
            "[模板匹配] 分数过低，认为没有找到朋友圈图标。"
        )
        return None

    center_x = int(max_loc[0] + tw / 2)
    center_y = int(max_loc[1] + th / 2)

    debug = image.copy()

    cv2.rectangle(
        debug,
        (max_loc[0], max_loc[1]),
        (max_loc[0] + tw, max_loc[1] + th),
        (0, 0, 255),
        2,
    )

    cv2.circle(
        debug,
        (center_x, center_y),
        8,
        (0, 255, 0),
        -1,
    )

    debug_path = DEBUG_DIR / "moments_template_match.png"

    cv2.imwrite(
        str(debug_path),
        debug,
    )

    print(
        f"[模板匹配] 调试图已保存："
        f"{debug_path.resolve()}"
    )

    return center_x, center_y


def click_moments_icon_by_image(window) -> None:
    """先单击“发现”，再双击发现页中的“朋友圈”，并保存阶段截图。"""
    image = capture_wechat_window(window)

    # 以后优先用模板，不再靠“第几个图标”
    template_point = find_moments_icon_by_template(
        image
    )

    if template_point is not None:
        icon_x, icon_y = template_point

        print(
            f"[发现入口] 已通过模板找到发现图标："
            f"({icon_x}, {icon_y})"
        )

        click_window_point(
            window,
            icon_x,
            icon_y,
        )

        time.sleep(2.0)

        after_image = capture_wechat_window(window)

        after_path = DEBUG_DIR / "after_click_discover.png"

        cv2.imwrite(
            str(after_path),
            after_image,
        )

        print(
            f"[阶段1] 发现页截图已保存：{after_path.resolve()}"
        )

        # 新版微信在“发现”页内展示朋友圈入口，需要再次双击。
        ref_w, ref_h = DISCOVER_REFERENCE_SIZE
        page_h, page_w = after_image.shape[:2]
        moments_x = int(round(DISCOVER_MOMENTS_POINT[0] * page_w / ref_w))
        moments_y = int(round(DISCOVER_MOMENTS_POINT[1] * page_h / ref_h))

        if not (0 <= moments_x < page_w and 0 <= moments_y < page_h):
            raise RuntimeError(
                f"发现页朋友圈入口坐标超出截图范围：({moments_x}, {moments_y})"
            )

        # 保存点击前的局部证据，便于确认微信版本是否改变了入口位置。
        crop_x1 = max(0, moments_x - 100)
        crop_y1 = max(0, moments_y - 65)
        crop_x2 = min(page_w, moments_x + 180)
        crop_y2 = min(page_h, moments_y + 65)
        cv2.imwrite(
            str(DEBUG_DIR / "discover_moments_entry_check.png"),
            after_image[crop_y1:crop_y2, crop_x1:crop_x2],
        )

        print(
            f"[阶段2] 准备双击发现页朋友圈入口："
            f"窗口内=({moments_x}, {moments_y})"
        )
        double_click_window_point(window, moments_x, moments_y)
        time.sleep(2.5)

        moments_image = capture_wechat_window(window)
        moments_path = DEBUG_DIR / "after_double_click_moments.png"
        cv2.imwrite(str(moments_path), moments_image)
        print(f"[阶段2] 双击后截图已保存：{moments_path.resolve()}")

        return

    # 第一次没有模板时，才走编号校准
    icons = find_left_sidebar_icons(image)

    print(f"识别到左侧图标数量：{len(icons)}")

    for index, point in enumerate(
        icons,
        start=1,
    ):
        print(
            f"  图标{index}: 窗口内坐标={point}"
        )

    debug = draw_debug_image(
        image,
        icons,
    )

    debug_path = DEBUG_DIR / "left_icons_detected.png"

    cv2.imwrite(
        str(debug_path),
        debug,
    )

    print(
        f"调试图已保存：{debug_path.resolve()}"
    )

    if len(icons) == 0:
        raise RuntimeError(
            "没有识别到左侧图标。"
        )

    print()
    print("第一次需要校准朋友圈图标。")
    print("请打开 left_icons_detected.png。")
    print("看红圈编号，输入朋友圈对应的编号。")
    print("这次选完后，会保存模板，以后不再按编号点击。")

    user_input = input(
        "请输入朋友圈图标编号："
    ).strip()

    if not user_input:
        raise RuntimeError(
            "没有输入朋友圈图标编号。"
        )

    try:
        target_index = int(user_input)
    except ValueError:
        raise RuntimeError(
            "输入的编号不是数字。"
        )

    if (
        target_index < 1
        or target_index > len(icons)
    ):
        raise RuntimeError(
            f"编号超出范围：{target_index}，"
            f"当前只有 {len(icons)} 个图标。"
        )

    icon_x, icon_y = icons[
        target_index - 1
    ]

    print(
        f"[校准] 本次选择第 {target_index} 个图标，"
        f"窗口内坐标=({icon_x}, {icon_y})"
    )

    save_moments_icon_template(
        image,
        icon_x,
        icon_y,
    )

    # 首次校准保存的是“发现”入口模板；后续仍由发现页二次双击进入朋友圈。
    click_window_point(
        window,
        icon_x,
        icon_y,
    )

    time.sleep(2.0)

    after_image = capture_wechat_window(window)

    after_path = DEBUG_DIR / "after_click_discover.png"

    cv2.imwrite(
        str(after_path),
        after_image,
    )

    print(
        f"[阶段1] 发现页截图已保存：{after_path.resolve()}"
    )

    ref_w, ref_h = DISCOVER_REFERENCE_SIZE
    page_h, page_w = after_image.shape[:2]
    moments_x = int(round(DISCOVER_MOMENTS_POINT[0] * page_w / ref_w))
    moments_y = int(round(DISCOVER_MOMENTS_POINT[1] * page_h / ref_h))
    double_click_window_point(window, moments_x, moments_y)
    time.sleep(2.5)
    moments_image = capture_wechat_window(window)
    cv2.imwrite(
        str(DEBUG_DIR / "after_double_click_moments.png"),
        moments_image,
    )
    print(
        f"[阶段2] 双击后截图已保存："
        f"{(DEBUG_DIR / 'after_double_click_moments.png').resolve()}"
    )


def main():
    round_index = 0

    while True:
        round_index += 1

        print()
        print("=" * 70)
        print(f"开始第 {round_index} 轮朋友圈点赞")
        print(f"目标：快速点赞 {ROUND_LIKE_COUNT} 个")
        print("=" * 70)

        # 每一轮重新清空本轮去重
        PROCESSED_POST_HASHES.clear()

        main_window = activate_wechat()

        # 优先尝试新版两步入口；入口或独立窗口验证失败时，
        # 回退到当前已有的第二条链路。
        first_chain_ok = try_first_moments_chain(main_window)
        moments_window = None

        if first_chain_ok:
            # 标题模板已经在右侧50%截图中确认，直接把当前展开窗口
            # 交给旧的滚动/点赞模块，不再重复依赖 PID 或顶层窗口枚举。
            moments_window = _MomentsRegionWindow(main_window)
            moments_window.set_focus()
            print("[第一链路] 朋友圈标题确认成功，开始自动滚动点赞。")

        if moments_window is None:
            print("[第二链路] 开始执行备用朋友圈入口流程。")
            click_moments_icon_by_image(main_window)
            try:
                moments_window = wait_for_moments_popup_window(
                    main_window,
                    timeout=10.0,
                )
            except Exception as exc:
                print(f"[第二链路] 独立窗口验证失败：{exc}")
                print("微信可能更新，请加快优化此系统")
                raise

        print("已经进入朋友圈独立窗口。")

        liked_count = 0
        stop_reason = ""

        while liked_count < ROUND_LIKE_COUNT:
            print()
            print("-" * 60)
            print(
                f"第 {round_index} 轮，"
                f"准备处理第 {liked_count + 1} 条朋友圈"
            )
            print(
                f"当前进度：{liked_count}/{ROUND_LIKE_COUNT}"
            )
            print("-" * 60)

            result = find_more_button_with_scroll(
                moments_window,
                max_scrolls=80,
            )

            if result == "not_found":
                print(
                    "没有找到可点赞按钮，退出本轮。"
                )

                stop_reason = "没有找到按钮"
                break

            if result == "liked":
                liked_count += 1

                print(
                    f"当前已完成点赞："
                    f"{liked_count}/{ROUND_LIKE_COUNT}"
                )

                scroll_target_window(
                    moments_window,
            wheel_amount=-1,
                    repeat=1,
                )

                time.sleep(0.8 if isinstance(moments_window, _MomentsRegionWindow) else 0.5)

                continue

            if result == "duplicate":
                print(
                    "当前朋友圈已经赞过或本轮处理过，"
                    "继续往下找。"
                )

                scroll_target_window(
                    moments_window,
            wheel_amount=-1,
                    repeat=1,
                )

                time.sleep(0.8 if isinstance(moments_window, _MomentsRegionWindow) else 0.5)

                continue

            print(
                f"未知结果：{result}，继续往下找。"
            )

            scroll_target_window(
                moments_window,
            wheel_amount=-1,
                repeat=1,
            )

            time.sleep(0.8 if isinstance(moments_window, _MomentsRegionWindow) else 0.5)

        if liked_count >= ROUND_LIKE_COUNT:
            stop_reason = f"已完成 {ROUND_LIKE_COUNT} 个点赞"

        print()
        print("=" * 70)
        print(f"第 {round_index} 轮结束")
        print(f"本轮点赞数量：{liked_count}")
        print(f"结束原因：{stop_reason}")
        print("=" * 70)

        close_moments_window(
            moments_window
        )

        print(
            "休息 30 分钟后，重新进入朋友圈开始下一轮。"
        )

        sleep_with_countdown(
            ROUND_REST_SECONDS
        )

if __name__ == "__main__":
    main()
