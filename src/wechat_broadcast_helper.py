from __future__ import annotations

import re
import time

import pyautogui
import pygetwindow as gw
import pyperclip
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

# 复用朋友圈脚本中的：
# ADB 5038、USB真机识别、内存截图、点击、返回、页面识别
import comment_like as base


SENT_LOG_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "sent_contacts.txt"
)


def normalize_contact_id(value: str) -> str:
    """
    统一联系人唯一标识，避免空格和大小写造成重复。
    """
    return (
        value.strip()
        .replace(" ", "")
        .lower()
    )


def load_sent_contacts() -> set[str]:
    """
    读取已经发送成功的联系人标识。
    """
    if not SENT_LOG_FILE.exists():
        return set()

    return {
        normalize_contact_id(line)
        for line in SENT_LOG_FILE.read_text(
            encoding="utf-8"
        ).splitlines()
        if normalize_contact_id(line)
    }


def is_sent(contact_id: str) -> bool:
    """
    精确判断联系人是否已经发送过。
    """
    normalized_id = normalize_contact_id(
        contact_id
    )

    return normalized_id in load_sent_contacts()


def mark_as_sent(contact_id: str) -> None:
    """
    最终发送成功后记录联系人标识。
    """
    normalized_id = normalize_contact_id(
        contact_id
    )

    if not normalized_id:
        return

    if is_sent(normalized_id):
        print(
            f"[去重记录] 已经存在："
            f"{normalized_id}"
        )
        return

    SENT_LOG_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with SENT_LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            normalized_id + "\n"
        )

    print(
        f"[去重记录] 已保存："
        f"{normalized_id}"
    )


def wait_for_compose_page(
    timeout: float = 300.0,
) -> bool:
    """
    等待用户在手机上选择联系人并进入群发编辑页。
    """
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1

        stage = get_current_broadcast_stage()

        if stage == "compose":
            print(
                "[页面检测] 已进入群发编辑页。"
            )
            return True

        if stage == "select_friends":
            print(
                f"[页面检测] 第 {attempt} 次："
                "当前仍在选择朋友页面。"
            )
        else:
            print(
                f"[页面检测] 第 {attempt} 次："
                f"当前页面={stage}"
            )

        time.sleep(1.2)

    print(
        "等待进入群发编辑页超时。"
    )

    return False




TESSERACT_EXE = Path(
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

if TESSERACT_EXE.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_EXE)

OCR_LANG = "chi_sim+eng"


@dataclass
class OCRLine:
    text: str
    box: tuple[int, int, int, int]
    confidence: float


def normalize_text(text: str) -> str:
    """统一OCR文字，便于关键词匹配。"""
    return (
        text.replace(" ", "")
        .replace("\t", "")
        .replace("\n", "")
        .replace("：", ":")
        .replace("丨", "")
        .strip()
    )


def ocr_lines(
    image: np.ndarray,
    region: Optional[tuple[float, float, float, float]] = None,
) -> list[OCRLine]:
    """
    从手机截图中按“行”提取OCR文字。

    region使用屏幕比例：
        (x1_ratio, y1_ratio, x2_ratio, y2_ratio)
    """
    height, width = image.shape[:2]

    offset_x = 0
    offset_y = 0
    target = image

    if region is not None:
        rx1, ry1, rx2, ry2 = region

        x1 = max(0, int(width * rx1))
        y1 = max(0, int(height * ry1))
        x2 = min(width, int(width * rx2))
        y2 = min(height, int(height * ry2))

        if x2 <= x1 or y2 <= y1:
            return []

        target = image[y1:y2, x1:x2]
        offset_x = x1
        offset_y = y1

    # 放大截图有助于识别微信列表文字
    scale = 1.6
    enlarged = cv2.resize(
        target,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)

    data = pytesseract.image_to_data(
        gray,
        lang=OCR_LANG,
        config="--psm 6",
        output_type=Output.DICT,
    )

    grouped: dict[
        tuple[int, int, int],
        list[tuple[int, str, float, tuple[int, int, int, int]]],
    ] = {}

    count = len(data.get("text", []))

    for index in range(count):
        raw_text = str(data["text"][index]).strip()

        if not raw_text:
            continue

        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0

        if confidence < 15:
            continue

        left = int(data["left"][index])
        top = int(data["top"][index])
        token_width = int(data["width"][index])
        token_height = int(data["height"][index])

        # 坐标缩回原始截图
        x1 = offset_x + int(left / scale)
        y1 = offset_y + int(top / scale)
        x2 = offset_x + int((left + token_width) / scale)
        y2 = offset_y + int((top + token_height) / scale)

        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )

        grouped.setdefault(key, []).append(
            (
                x1,
                raw_text,
                confidence,
                (x1, y1, x2, y2),
            )
        )

    lines: list[OCRLine] = []

    for tokens in grouped.values():
        tokens.sort(key=lambda item: item[0])

        text = "".join(item[1] for item in tokens)
        confidence = sum(item[2] for item in tokens) / len(tokens)

        x1 = min(item[3][0] for item in tokens)
        y1 = min(item[3][1] for item in tokens)
        x2 = max(item[3][2] for item in tokens)
        y2 = max(item[3][3] for item in tokens)

        lines.append(
            OCRLine(
                text=text,
                box=(x1, y1, x2, y2),
                confidence=confidence,
            )
        )

    lines.sort(key=lambda item: (item.box[1], item.box[0]))
    return lines


def find_text_center(
    image: np.ndarray,
    keywords: Iterable[str],
    region: Optional[tuple[float, float, float, float]] = None,
) -> Optional[tuple[int, int, str]]:
    """
    通过截图OCR寻找包含关键词的文字行中心。
    """
    normalized_keywords = [
        normalize_text(keyword)
        for keyword in keywords
    ]

    matches: list[
        tuple[float, tuple[int, int, str]]
    ] = []

    for line in ocr_lines(image, region=region):
        normalized_line = normalize_text(line.text)

        for keyword in normalized_keywords:
            if keyword and keyword in normalized_line:
                x1, y1, x2, y2 = line.box

                center_x = (x1 + x2) // 2
                center_y = (y1 + y2) // 2

                # 优先识别置信度高、文字更完整的结果
                score = (
                    line.confidence
                    + len(keyword) * 10
                    - abs(len(normalized_line) - len(keyword))
                )

                matches.append(
                    (
                        score,
                        (
                            center_x,
                            center_y,
                            line.text,
                        ),
                    )
                )

    if not matches:
        return None

    matches.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return matches[0][1]

def click_text_by_screenshot(
    keywords: str | list[str],
    *,
    region: Optional[
        tuple[float, float, float, float]
    ] = None,
    wait_after: float = 1.5,
) -> bool:
    """
    通过ADB截图OCR识别文字，并点击文字所在位置。
    """
    if isinstance(keywords, str):
        keywords = [keywords]

    image = base.capture_screen()

    result = find_text_center(
        image,
        keywords,
        region=region,
    )

    if result is None:
        print(
            f"[截图文字定位] 没有识别到："
            f"{'/'.join(keywords)}"
        )
        return False

    text_x, text_y, recognized_text = result

    print(
        f"[截图文字定位] 识别到“{recognized_text}”，"
        f"点击坐标：({text_x}, {text_y})"
    )

    base.tap_xy(
        text_x,
        text_y,
        wait_after=wait_after,
    )

    return True

def click_confirm_button() -> bool:
    """
    通过识别右下角微信绿色按钮来点击“选中”。
    """
    image = base.capture_screen()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # 微信选中按钮的绿色范围
    lower_green = np.array([40, 100, 100])
    upper_green = np.array([90, 255, 255])
    mask = cv2.inRange(hsv, lower_green, upper_green)
    
    # 寻找右下角区域的绿色连通域
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # 按钮通常在屏幕右下角，且宽度较大
        if w > 100 and h > 50 and x > image.shape[1] * 0.6 and y > image.shape[0] * 0.8:
            print(f"[定位] 识别到“选中”按钮，坐标: ({x+w//2}, {y+h//2})")
            base.tap_xy(x + w//2, y + h//2, wait_after=2.0)
            return True
    return False

def screenshot_has_text(
    keywords: str | list[str],
    *,
    region: Optional[tuple[float, float, float, float]] = None,
) -> bool:
    if isinstance(keywords, str):
        keywords = [keywords]

    image = base.capture_screen()

    return (
        find_text_center(
            image,
            keywords,
            region=region,
        )
        is not None
    )


def wait_for_text(
    keywords: str | list[str],
    *,
    timeout: float = 20.0,
    interval: float = 1.0,
    region: Optional[tuple[float, float, float, float]] = None,
) -> bool:
    if isinstance(keywords, str):
        keywords = [keywords]

    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1

        image = base.capture_screen()

        match = find_text_center(
            image,
            keywords,
            region=region,
        )

        if match is not None:
            print(
                f"[页面等待] 第 {attempt} 次截图"
                f"识别到：{match[2]}"
            )
            return True

        print(
            f"[页面等待] 第 {attempt} 次暂未识别到："
            f"{'/'.join(keywords)}"
        )

        time.sleep(interval)

    return False


def swipe_up(
    distance_ratio: float = 0.52,
    duration_ms: int = 750,
) -> None:
    image = base.capture_screen()
    height, width = image.shape[:2]

    start_x = width // 2
    start_y = int(height * 0.82)
    end_y = int(start_y - height * distance_ratio)

    end_y = max(
        int(height * 0.20),
        end_y,
    )

    print(
        f"[截图导航] 向上滑动："
        f"({start_x}, {start_y}) -> "
        f"({start_x}, {end_y})"
    )

    result = base.run_device_adb(
        [
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(start_x),
            str(end_y),
            str(duration_ms),
        ]
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr
            or result.stdout
            or "页面滑动失败"
        )

    time.sleep(1.1)


def ensure_me_page() -> np.ndarray:
    """
    使用朋友圈脚本的截图页面判断进入“我”页面。
    """
    base.open_wechat()
    unknown_count = 0

    for attempt in range(12):
        image = base.capture_screen()
        result = base.classify_page(image)

        print(
            f"[页面判断] {result.page} "
            f"| {result.reason}"
        )

        if result.page == "me":
            print("当前已经在微信“我”页面")
            return image

        if result.page in {
            "moments",
            "possible_moments",
        }:
            print("当前在朋友圈，返回一次。")
            base.press_back()
            unknown_count = 0
            continue

        if result.nav is not None:
            me_x, me_y = result.nav.centers[3]

            print(
                f"[截图导航] 点击“我”："
                f"({me_x}, {me_y})"
            )

            base.tap_xy(
                me_x,
                me_y,
                wait_after=1.8,
            )

            unknown_count = 0
            continue

        unknown_count += 1

        if unknown_count < 4:
            print(
                f"页面暂未识别，先等待："
                f"{unknown_count}/4"
            )
            time.sleep(1.2)
            continue

        print("连续多次无法识别，只返回一次。")
        base.press_back()
        unknown_count = 0

    raise RuntimeError(
        "多次截图判断后仍未进入微信“我”页面"
    )


def tap_confirm_button() -> None:
    """
    直接点击屏幕右下角的“选中”按钮。
    根据截图，该按钮通常在屏幕右下角 85% 高度以上、65% 宽度以右。
    """
    height, width = base.capture_screen().shape[:2]
    # 根据你的截图，选中(X)按钮大约在 x=850, y=1000 附近
    print("[自动操作] 点击“选中”按钮...")
    base.tap_xy(int(width * 0.85), int(height * 0.95), wait_after=2.5)

def tap_send_button() -> None:
    """
    直接点击编辑页右上角的“发送”按钮。
    """
    height, width = base.capture_screen().shape[:2]
    # 发送按钮通常在右上角
    print("[自动操作] 点击“发送”按钮...")
    base.tap_xy(int(width * 0.95), int(height * 0.08), wait_after=1.5)

def scroll_and_click(
    keywords: str | list[str],
    *,
    max_swipes: int = 7,
    region: Optional[tuple[float, float, float, float]] = None,
) -> bool:
    """
    当前页面找不到指定文字时向上滑动，再截图识别。
    """
    for attempt in range(max_swipes + 1):
        if click_text_by_screenshot(
            keywords,
            region=region,
            wait_after=1.5,
        ):
            return True

        if attempt >= max_swipes:
            break

        print(
            f"[截图导航] 当前屏幕未找到目标，"
            f"继续滑动：{attempt + 1}/{max_swipes}"
        )

        swipe_up()

    return False


def is_select_friends_page_by_circles(
    image: np.ndarray,
) -> bool:
    """
    根据左侧白色/绿色选择圆圈，
    判断当前是否为“选择朋友”页面。
    """
    height, width = image.shape[:2]

    # 1. 识别已有函数找到的白色未选圆圈
    white_circles = find_unselected_circle_candidates(
        image
    )

    # 2. 识别左侧绿色已选圆圈
    left_x1 = 0
    left_x2 = int(width * 0.15)

    left_y1 = int(height * 0.16)
    left_y2 = int(height * 0.88)

    left_roi = image[
        left_y1:left_y2,
        left_x1:left_x2,
    ]

    hsv = cv2.cvtColor(
        left_roi,
        cv2.COLOR_BGR2HSV,
    )

    green_mask = cv2.inRange(
        hsv,
        np.array(
            [35, 70, 70],
            dtype=np.uint8,
        ),
        np.array(
            [95, 255, 255],
            dtype=np.uint8,
        ),
    )

    green_mask = cv2.morphologyEx(
        green_mask,
        cv2.MORPH_CLOSE,
        np.ones((5, 5), np.uint8),
    )

    contours, _ = cv2.findContours(
        green_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    green_circles = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(
            contour
        )

        # 绿色圆圈直径通常约占屏幕宽度4%～9%
        if not (
            int(width * 0.035)
            <= w
            <= int(width * 0.095)
        ):
            continue

        if not (
            int(width * 0.035)
            <= h
            <= int(width * 0.095)
        ):
            continue

        if abs(w - h) > int(width * 0.025):
            continue

        center_x = x + w // 2
        center_y = left_y1 + y + h // 2

        if center_x > int(width * 0.12):
            continue

        green_circles.append(
            (
                center_x,
                center_y,
            )
        )

    # 3. 检查右下角绿色“选中”按钮
    bottom_roi = image[
        int(height * 0.82):
        int(height * 0.94),

        int(width * 0.68):
        int(width * 0.98),
    ]

    bottom_hsv = cv2.cvtColor(
        bottom_roi,
        cv2.COLOR_BGR2HSV,
    )

    bottom_green_mask = cv2.inRange(
        bottom_hsv,
        np.array(
            [35, 70, 70],
            dtype=np.uint8,
        ),
        np.array(
            [95, 255, 255],
            dtype=np.uint8,
        ),
    )

    bottom_green_ratio = float(
        np.mean(bottom_green_mask > 0)
    )

    total_circles = (
        len(white_circles)
        + len(green_circles)
    )

    print(
        "[选择页圆圈判断] "
        f"白圈={len(white_circles)}，"
        f"绿圈={len(green_circles)}，"
        f"总数={total_circles}，"
        f"底部绿色比例={bottom_green_ratio:.3f}"
    )

    # 有多个选择圈，基本可以确认是选择朋友页面
    if total_circles >= 3:
        return True

    # 至少有一个选择圈，同时右下角存在绿色按钮
    if (
        total_circles >= 1
        and bottom_green_ratio >= 0.05
    ):
        return True

    return False

def get_unprocessed_contact(image: np.ndarray, sent_set: set) -> Optional[str]:
    """
    通过OCR扫描群发助手首页的历史记录，找到未发送过的联系人名。
    """
    lines = ocr_lines(image)
    for line in lines:
        # 假设 OCR 识别到了类似 "已发送给 1 个朋友: Scarlett" 的格式
        if "已发送给" in line.text:
            contact_name = line.text.split(":")[-1].strip()
            if contact_name not in sent_set:
                return contact_name
    return None


def get_current_broadcast_stage() -> str:
    """
    截图识别当前所在页面。

    判断原则：
    1. 先识别特征明确的页面文字；
    2. 再使用圆圈识别作为选择朋友页的辅助判断；
    3. 避免把辅助功能页面图标误判为联系人圆圈。
    """
    image = base.capture_screen()
    page_result = base.classify_page(image)

    # =====================================================
    # 1. 最终发送确认弹窗
    # =====================================================
    if (
        find_final_confirm_send_button(image)
        is not None
    ):
        print(
            "[页面判断] 当前是最终发送确认页"
        )
        return "final_confirm"

    # =====================================================
    # 2. 识别页面顶部
    # =====================================================
    top_lines = ocr_lines(
        image,
        region=(0.0, 0.02, 1.0, 0.34),
    )

    top_text = normalize_text(
        "".join(
            line.text
            for line in top_lines
        )
    )

    print(
        f"[顶部页面OCR] "
        f"{top_text[:160]}"
    )

    # 群发消息编辑页
    if (
        "你将发消息给" in top_text
        or "将发消息给" in top_text
        or (
            "群发" in top_text
            and "朋友" in top_text
        )
    ):
        print(
            "[页面判断] 已识别到群发编辑页"
        )
        return "compose"

    # =====================================================
    # 3. 先OCR整页文字，识别明确页面
    # =====================================================
    lines = ocr_lines(image)

    text = normalize_text(
        "".join(
            line.text
            for line in lines
        )
    )

    print(
        f"[当前页面OCR] "
        f"{text[:220]}"
    )

    # -----------------------------------------------------
    # 辅助功能页面必须放在“群发助手”判断前面
    # 因为该页面本身也包含“群发助手”
    # -----------------------------------------------------
    if (
        "辅助功能" in text
        and (
            "已启用的功能" in text
            or "未启用的功能" in text
            or "腾讯新闻" in text
            or "微信支付" in text
            or "微信运动" in text
        )
    ):
        print(
            "[页面判断] 已识别到辅助功能页面"
        )
        return "accessibility"

    # 其他功能页面
    if (
        "其他功能" in text
        and (
            "辅助功能" in text
            or "发现页管理" in text
            or "听一听" in text
        )
    ):
        print(
            "[页面判断] 已识别到其他功能页面"
        )
        return "other_features"

    # 设置页面
    if (
        "设置" in text
        and (
            "界面与显示" in text
            or "朋友权限" in text
            or "存储空间" in text
            or "其他功能" in text
            or "聊天记录管理" in text
            or "帮助与反馈" in text
        )
    ):
        print(
            "[页面判断] 已识别到设置页面"
        )
        return "settings"

    # 群发历史记录页面
    if (
        "新建群发" in text
        or (
            "群发助手" in text
            and "再发一条" in text
        )
    ):
        print(
            "[页面判断] 已识别到群发历史页面"
        )
        return "broadcast_history"

    # 群发助手详情页
    if (
        "群发助手" in text
        and (
            "开始群发" in text
            or "清空此功能消息记录" in text
            or "停用" in text
        )
    ):
        print(
            "[页面判断] 已识别到群发助手详情页"
        )
        return "broadcast_detail"

    # 选择朋友页面：优先使用明确文字
    if (
        "选择朋友" in text
        and (
            "从标签导入" in text
            or "从群聊导入" in text
            or "星标朋友" in text
            or "选中" in text
        )
    ):
        print(
            "[页面判断] 根据页面文字确认"
            "当前是选择朋友页面"
        )
        return "select_friends"

    # 编辑页整页兜底识别
    if (
        "你将发消息给" in text
        or "将发消息给" in text
    ):
        return "compose"

    # =====================================================
    # 4. 只有文字无法明确判断时，
    # 才使用圆圈特征辅助判断
    # =====================================================
    if is_select_friends_page_by_circles(
        image
    ):
        print(
            "[页面判断] OCR无法明确判断，"
            "根据圆圈和底部按钮辅助确认"
            "为选择朋友页面"
        )
        return "select_friends"

    # =====================================================
    # 5. 使用底部导航识别微信主页面
    # =====================================================
    if page_result.page == "me":
        return "me"

    if page_result.page in {
        "wechat_home",
        "contacts",
        "discover",
        "wechat_main_unknown_tab",
    }:
        return "wechat_main"

    if page_result.page in {
        "moments",
        "possible_moments",
    }:
        return "moments"

    return "unknown"




def open_broadcast_selection_page() -> str:
    """
    根据当前页面断点续跑。

    不再强制从：
    我 -> 设置

    而是识别当前在哪个页面，
    然后只执行下一步。
    """
    base.open_wechat()

    unknown_count = 0
    max_steps = 25

    for step in range(1, max_steps + 1):
        stage = get_current_broadcast_stage()

        print(
            f"[断点续跑] 第 {step}/{max_steps} 步，"
            f"当前页面：{stage}"
        )

        # 已经到选择朋友页面，直接停止导航
        if stage == "select_friends":
            print("=" * 68)
            print("当前已经在“选择朋友”页面。")
            print("不再重新进入“我”和“设置”。")
            print("请使用“从标签导入”选择本次联系人。")
            print("=" * 68)

            return "select_friends"

        # 已经在消息编辑页面，也不用再导航
        if stage == "compose":
            print("=" * 68)
            print("当前已经在群发消息编辑页面。")
            print("不再重复进入群发助手。")
            print("=" * 68)

            return "compose"

        # 当前在朋友圈
        if stage == "moments":
            print("当前在朋友圈，返回一次。")

            base.press_back()
            unknown_count = 0
            continue

        # 当前在微信主界面
        if stage == "wechat_main":
            image = base.capture_screen()
            page_result = base.classify_page(image)

            if page_result.nav is None:
                raise RuntimeError(
                    "识别到微信主界面，"
                    "但没有识别到底部导航栏。"
                )

            me_x, me_y = page_result.nav.centers[3]

            print(
                f"点击底部“我”："
                f"({me_x}, {me_y})"
            )

            base.tap_xy(
                me_x,
                me_y,
                wait_after=1.8,
            )

            unknown_count = 0
            continue

        # 当前在“我”
        if stage == "me":
            if not click_text_by_screenshot(
                "设置",
                region=(0.0, 0.60, 1.0, 0.88),
                wait_after=1.6,
            ):
                raise RuntimeError(
                    "已经在“我”页面，"
                    "但截图没有识别到“设置”。"
                )

            unknown_count = 0
            continue

        # 当前在设置
        if stage == "settings":
            if not scroll_and_click(
                "其他功能",
                max_swipes=8,
                region=(0.0, 0.16, 1.0, 0.88),
            ):
                raise RuntimeError(
                    "已经在设置页面，"
                    "但没有找到“其他功能”。"
                )

            unknown_count = 0
            continue

        # 当前在其他功能
        if stage == "other_features":
            if not click_text_by_screenshot(
                "辅助功能",
                region=(0.0, 0.18, 1.0, 0.70),
                wait_after=1.6,
            ):
                raise RuntimeError(
                    "已经在其他功能页面，"
                    "但没有找到“辅助功能”。"
                )

            unknown_count = 0
            continue

        # 当前在辅助功能
        if stage == "accessibility":
            if not scroll_and_click(
                "群发助手",
                max_swipes=4,
                region=(0.0, 0.12, 1.0, 0.88),
            ):
                raise RuntimeError(
                    "已经在辅助功能页面，"
                    "但没有找到“群发助手”。"
                )

            unknown_count = 0
            continue

        # 当前在群发助手详情页
        if stage == "broadcast_detail":
            if not click_text_by_screenshot(
                "开始群发",
                region=(0.0, 0.25, 1.0, 0.68),
                wait_after=1.8,
            ):
                raise RuntimeError(
                    "已经在群发助手详情页，"
                    "但没有找到“开始群发”。"
                )

            unknown_count = 0
            continue

        # 当前在群发历史记录页
        if stage == "broadcast_history":
            if not scroll_and_click(
                "新建群发",
                max_swipes=6,
                region=(0.0, 0.45, 1.0, 0.95),
            ):
                raise RuntimeError(
                    "已经在群发历史页，"
                    "但没有找到“新建群发”。"
                )

            unknown_count = 0
            continue

        # 暂时无法识别时先等待，不乱按返回
        unknown_count += 1

        print(
            f"页面暂时无法识别，"
            f"连续 unknown={unknown_count}，"
            "先等待，不按返回键。"
        )

        if unknown_count <= 4:
            time.sleep(1.3)
            continue

        raise RuntimeError(
            "连续多次无法识别当前群发页面。"
        )

    raise RuntimeError(
        f"执行 {max_steps} 步后，"
        "仍未到达选择朋友页面。"
    )

def find_all_checkboxes(image: np.ndarray) -> list[tuple[int, int]]:
    """
    通过颜色特征识别：直接遍历轮廓并过滤颜色。
    """
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # 1. 寻找所有类似“圆圈”的轮廓（二值化）
    _, thresh = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    coords = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        
        # 1. 尺寸过滤：确保是圆圈
        if 35 < w < 55 and 35 < h < 55 and abs(w - h) < 8:
            # 2. 坐标过滤：X 在左侧 20%，Y 只要大于屏幕高度 5% 即可，避免点到标题栏
            if x < int(width * 0.2) and y > int(height * 0.05): 
                cx, cy = x + w // 2, y + h // 2
                
                # 额外保护：确保点击位置不在屏幕最上方的搜索框区域
                if cy < int(height * 0.15): continue 
                
                # 去重：确保不会重复点击同一个圆圈
                if not any(abs(cx - px) < 30 and abs(cy - py) < 30 for px, py in coords):
                    coords.append((cx, cy))
    
    return sorted(coords, key=lambda p: p[1]) 


def find_unselected_circle_candidates(
    image: np.ndarray,
) -> list[tuple[int, int]]:
    """
    采用ADB截图 + OpenCV圆形检测，识别左侧未勾选的白色圆圈。

    只识别、标记坐标，不在本函数中执行点击。
    """
    height, width = image.shape[:2]

    # 只分析联系人列表左侧圆圈所在区域
    roi_x1 = 0
    roi_x2 = int(width * 0.16)

    # 排除顶部标题、搜索框、导入入口和底部已选联系人栏
    roi_y1 = int(height * 0.18)
    roi_y2 = int(height * 0.87)

    roi = image[
        roi_y1:roi_y2,
        roi_x1:roi_x2,
    ]

    if roi.size == 0:
        return []

    gray = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.GaussianBlur(
        gray,
        (7, 7),
        1.6,
    )

    # 尺寸全部根据截图宽度计算，不再写死像素
    min_radius = max(
        12,
        int(width * 0.018),
    )

    max_radius = max(
        min_radius + 6,
        int(width * 0.045),
    )

    min_distance = max(
        55,
        int(height * 0.050),
    )

    circles = cv2.HoughCircles(
        gray,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=min_distance,
        param1=85,
        param2=14,
        minRadius=min_radius,
        maxRadius=max_radius,
    )

    if circles is None:
        print(
            "[圆圈识别] HoughCircles没有检测到圆圈"
        )
        return []

    hsv = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2HSV,
    )

    # 已选中圆圈为微信绿色
    green_mask = cv2.inRange(
        hsv,
        np.array(
            [35, 70, 70],
            dtype=np.uint8,
        ),
        np.array(
            [95, 255, 255],
            dtype=np.uint8,
        ),
    )

    candidates: list[tuple[int, int]] = []

    rounded_circles = np.round(
        circles[0]
    ).astype(int)

    for local_x, local_y, radius in rounded_circles:
        # 圆圈必须真正位于左侧勾选栏
        absolute_x = roi_x1 + local_x
        absolute_y = roi_y1 + local_y

        if not (
            int(width * 0.015)
            <= absolute_x
            <= int(width * 0.115)
        ):
            continue

        if not (
            int(height * 0.20)
            <= absolute_y
            <= int(height * 0.86)
        ):
            continue

        patch_radius = int(radius * 1.35)

        patch_x1 = max(
            0,
            local_x - patch_radius,
        )

        patch_y1 = max(
            0,
            local_y - patch_radius,
        )

        patch_x2 = min(
            roi.shape[1],
            local_x + patch_radius,
        )

        patch_y2 = min(
            roi.shape[0],
            local_y + patch_radius,
        )

        patch_gray = gray[
            patch_y1:patch_y2,
            patch_x1:patch_x2,
        ]

        patch_green = green_mask[
            patch_y1:patch_y2,
            patch_x1:patch_x2,
        ]

        if patch_gray.size == 0:
            continue

        green_ratio = float(
            np.mean(patch_green > 0)
        )

        # 已选中圆圈包含大面积绿色，直接排除
        if green_ratio > 0.045:
            print(
                f"[圆圈识别] 排除绿色已选圆圈："
                f"({absolute_x}, {absolute_y})，"
                f"绿色比例={green_ratio:.3f}"
            )
            continue

        patch_height, patch_width = (
            patch_gray.shape
        )

        yy, xx = np.ogrid[
            :patch_height,
            :patch_width,
        ]

        center_patch_x = (
            local_x - patch_x1
        )

        center_patch_y = (
            local_y - patch_y1
        )

        distance = np.sqrt(
            (xx - center_patch_x) ** 2
            + (yy - center_patch_y) ** 2
        )

        # 白色圆圈内部
        inner_mask = (
            distance <= radius * 0.60
        )

        # 灰色圆圈边缘
        ring_mask = (
            (distance >= radius * 0.72)
            & (distance <= radius * 1.18)
        )

        if (
            not np.any(inner_mask)
            or not np.any(ring_mask)
        ):
            continue

        inner_pixels = patch_gray[
            inner_mask
        ]

        ring_pixels = patch_gray[
            ring_mask
        ]

        # 未勾选圆圈中间应当基本为白色
        inner_white_ratio = float(
            np.mean(inner_pixels >= 215)
        )

        # 圆环应存在一定比例的灰色边缘
        ring_gray_ratio = float(
            np.mean(
                (ring_pixels >= 105)
                & (ring_pixels <= 235)
            )
        )

        print(
            f"[圆圈候选] 坐标="
            f"({absolute_x}, {absolute_y})，"
            f"半径={radius}，"
            f"绿色={green_ratio:.3f}，"
            f"内部白色={inner_white_ratio:.3f}，"
            f"灰色圆环={ring_gray_ratio:.3f}"
        )

        if inner_white_ratio < 0.55:
            continue

        if ring_gray_ratio < 0.12:
            continue

        duplicate = any(
            abs(absolute_x - old_x)
            < int(width * 0.025)
            and abs(absolute_y - old_y)
            < int(height * 0.025)
            for old_x, old_y in candidates
        )

        if duplicate:
            continue

        candidates.append(
            (
                absolute_x,
                absolute_y,
            )
        )

    candidates.sort(
        key=lambda point: point[1]
    )

    return candidates

def select_all_friends_automatically(max_friends: int = 200) -> None:
    selected_count = 0
    # 存储本屏已点击的坐标，防止重复点击
    clicked_coords = [] 
    
    print(f"\n[全自动] 开始自动选择，目标：{max_friends}人...")

    while selected_count < max_friends:
        image = base.capture_screen()
        # 调用你现有的识别函数
        candidates = find_unselected_circle_candidates(image)
        
        found_new = False
        for cx, cy in candidates:
            # 去重：如果这个坐标离之前点击过的坐标太近，视为同一个人，跳过
            if any(abs(cx - px) < 40 and abs(cy - py) < 40 for px, py in clicked_coords):
                continue
            
            # 点击勾选
            base.tap_xy(cx, cy, wait_after=0.3)
            clicked_coords.append((cx, cy))
            selected_count += 1
            found_new = True
            print(f"已勾选第 {selected_count} 人 (坐标: {cx}, {cy})")
            
            if selected_count >= max_friends:
                break
        
        # 滑动翻页逻辑
        if selected_count < max_friends:
            print("当前页已处理完，正在上滑...")
            swipe_up(distance_ratio=0.5)
            time.sleep(1.5) # 滑动后必须等待加载
            clicked_coords = [] # 滑动后坐标系已变，清空记录
            
    print(
        f"\n[完成] 本轮识别处理结束，"
        f"程序计数={selected_count}。"
    )

    # confirm = input(
    #     "请查看手机底部实际人数。"
    #     "确认联系人正确后，输入 y 点击“选中”；"
    #     "其他输入停止："
    # ).strip().lower()

    # if confirm != "y":
    #     print("已停止，没有点击“选中”。")
    #     return False

    # 不使用OCR读取白色的“选中”文字，
    # 直接识别右下角绿色按钮
    if not click_confirm_button():
        print(
            "没有识别到右下角绿色“选中”按钮，"
            "请在手机上手动点击。"
        )
        return False

    print("已经点击绿色“选中”按钮，等待进入编辑页。")

    # 必须验证已经进入编辑页
    for attempt in range(10):
        time.sleep(1.0)

        stage = get_current_broadcast_stage()

        print(
            f"[进入编辑页验证] "
            f"{attempt + 1}/10，当前页面={stage}"
        )

        if stage == "compose":
            print("已经确认进入群发消息编辑页。")
            return True

        if stage == "select_friends":
            print("当前仍在选择朋友页面，继续等待。")
            continue

    print(
        "点击后仍未确认进入编辑页，"
        "停止后续操作。"
    )

    return False

def preview_unselected_contacts() -> None:
    """
    对当前手机截图识别未勾选白色圆圈，
    并在OpenCV窗口中标记识别结果。

    不保存截图，不自动点击。
    """
    image = base.capture_screen()

    candidates = (
        find_unselected_circle_candidates(
            image
        )
    )

    debug_image = image.copy()

    for index, (x, y) in enumerate(
        candidates,
        start=1,
    ):
        # 红色圆圈标出识别位置
        cv2.circle(
            debug_image,
            (x, y),
            int(image.shape[1] * 0.035),
            (0, 0, 255),
            4,
        )

        cv2.putText(
            debug_image,
            str(index),
            (
                x + int(image.shape[1] * 0.045),
                y + 10,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 255),
            3,
            cv2.LINE_AA,
        )

    print(
        f"[联系人预览] 本屏识别到 "
        f"{len(candidates)} 个未选中白色圆圈"
    )

    for index, point in enumerate(
        candidates,
        start=1,
    ):
        print(
            f"  未选中{index}："
            f"坐标={point}"
        )

    if candidates:
        print(
            "已经在识别窗口中用红圈标出，"
            "请核对是否全部对应未选联系人。"
        )
    else:
        print(
            "当前截图仍未识别到未选中圆圈。"
        )

    # 只在电脑窗口显示，不写入硬盘
    base.show_image(
        "未选中联系人截图识别",
        debug_image,
    )

    cv2.waitKey(1)

def find_green_send_button(
    image: np.ndarray,
) -> Optional[tuple[int, int]]:
    """
    通过绿色矩形识别群发编辑页输入框右侧的“发送”按钮。
    """
    height, width = image.shape[:2]

    # 发送按钮只会位于屏幕右侧、中下部
    x1 = int(width * 0.72)
    x2 = int(width * 0.99)

    y1 = int(height * 0.42)
    y2 = int(height * 0.72)

    roi = image[y1:y2, x1:x2]

    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2HSV,
    )

    green_mask = cv2.inRange(
        hsv,
        np.array(
            [35, 70, 70],
            dtype=np.uint8,
        ),
        np.array(
            [95, 255, 255],
            dtype=np.uint8,
        ),
    )

    green_mask = cv2.morphologyEx(
        green_mask,
        cv2.MORPH_CLOSE,
        np.ones((9, 9), np.uint8),
    )

    contours, _ = cv2.findContours(
        green_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    candidates = []

    for contour in contours:
        x, y, box_width, box_height = (
            cv2.boundingRect(contour)
        )

        absolute_x = x1 + x
        absolute_y = y1 + y

        if box_width < int(width * 0.08):
            continue

        if box_width > int(width * 0.28):
            continue

        if box_height < int(height * 0.025):
            continue

        if box_height > int(height * 0.10):
            continue

        if absolute_x < int(width * 0.70):
            continue

        area = box_width * box_height

        candidates.append(
            (
                area,
                (
                    absolute_x + box_width // 2,
                    absolute_y + box_height // 2,
                ),
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return candidates[0][1]



def wait_for_final_confirm_send_button(
    timeout: float = 10.0,
    interval: float = 0.5,
) -> Optional[tuple[int, int]]:
    """
    点击第一次发送后，等待二次确认弹窗出现。
    """
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1

        image = base.capture_screen()

        point = find_final_confirm_send_button(
            image
        )

        if point is not None:
            print(
                f"[二次确认等待] 第 {attempt} 次截图"
                f"识别到最终发送按钮：{point}"
            )

            return point

        print(
            f"[二次确认等待] 第 {attempt} 次"
            "暂未识别到最终发送按钮。"
        )

        time.sleep(interval)

    return None


def find_final_confirm_send_button(
    image: np.ndarray,
) -> Optional[tuple[int, int]]:
    """
    识别二次确认弹窗右下方绿色“发送”按钮。
    """
    height, width = image.shape[:2]

    # 只搜索屏幕下半部分右侧
    roi_x1 = int(width * 0.42)
    roi_x2 = int(width * 0.96)

    roi_y1 = int(height * 0.62)
    roi_y2 = int(height * 0.94)

    roi = image[
        roi_y1:roi_y2,
        roi_x1:roi_x2,
    ]

    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2HSV,
    )

    green_mask = cv2.inRange(
        hsv,
        np.array([30, 60, 60], dtype=np.uint8),
        np.array([100, 255, 255], dtype=np.uint8),
    )

    green_mask = cv2.morphologyEx(
        green_mask,
        cv2.MORPH_CLOSE,
        np.ones((13, 13), np.uint8),
    )

    contours, _ = cv2.findContours(
        green_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    candidates = []

    for contour in contours:
        x, y, box_width, box_height = (
            cv2.boundingRect(contour)
        )

        center_x = (
            roi_x1 + x + box_width // 2
        )

        center_y = (
            roi_y1 + y + box_height // 2
        )

        # 二次确认按钮比较宽
        if not (
            int(width * 0.20)
            <= box_width
            <= int(width * 0.52)
        ):
            continue

        if not (
            int(height * 0.025)
            <= box_height
            <= int(height * 0.11)
        ):
            continue

        if center_x < int(width * 0.55):
            continue

        if center_y < int(height * 0.68):
            continue

        area = float(
            box_width * box_height
        )

        candidates.append(
            (
                area,
                (
                    center_x,
                    center_y,
                ),
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    point = candidates[0][1]

    print(
        f"[二次确认] 识别到最终发送按钮："
        f"{point}"
    )

    return point


def wait_for_final_confirm_send_button(
    timeout: float = 12.0,
) -> Optional[tuple[int, int]]:
    """
    点击第一次发送后，等待二次确认弹窗出现。
    """
    start_time = time.time()
    attempt = 0

    while time.time() - start_time < timeout:
        attempt += 1

        image = base.capture_screen()

        point = find_final_confirm_send_button(
            image
        )

        if point is not None:
            print(
                f"[二次确认等待] 第 {attempt} 次"
                "截图识别成功。"
            )

            return point

        print(
            f"[二次确认等待] 第 {attempt} 次"
            "暂未识别到最终发送按钮。"
        )

        time.sleep(0.6)

    return None


def confirm_and_click_final_send() -> bool:
    """
    二次确认弹窗出现后，等待用户输入y，
    再点击最终发送按钮。
    """
    point = wait_for_final_confirm_send_button(
        timeout=12.0,
    )

    if point is None:
        print(
            "没有识别到二次确认页的发送按钮，"
            "程序停止，不执行最终发送。"
        )

        return False

    # confirm = input(
    #     "\n请核对二次确认页的收件人。"
    #     "确认最终发送请输入 y："
    # ).strip().lower()

    # if confirm != "y":
    #     print(
    #         "没有执行最终发送，"
    #         "当前确认弹窗保持不动。"
    #     )

    #     return False

    # 点击前重新截图校准按钮坐标
    fresh_image = base.capture_screen()

    fresh_point = (
        find_final_confirm_send_button(
            fresh_image
        )
    )

    if fresh_point is None:
        print(
            "点击前没有重新识别到最终发送按钮，"
            "为避免误点，本次停止。"
        )

        return False

    send_x, send_y = fresh_point

    print(
        f"[最终发送] 点击坐标："
        f"({send_x}, {send_y})"
    )

    base.tap_xy(
        send_x,
        send_y,
        wait_after=2.5,
    )

    # 验证确认弹窗是否消失
    verify_image = base.capture_screen()

    if (
        find_final_confirm_send_button(
            verify_image
        )
        is not None
    ):
        print(
            "点击后确认弹窗仍存在，"
            "不重复点击，请人工检查。"
        )

        return False

    print(
        "二次确认弹窗已经消失，"
        "最终发送已执行。"
    )

    return True



def fill_message_by_clipboard(
    message: str,
) -> bool:
    """
    通过Windows剪贴板和scrcpy把文字粘贴到微信输入框。

    只填入文字，不点击发送。
    """
    # confirm = input(
    #     f"\n准备粘贴以下内容：\n"
    #     f"{message}\n\n"
    #     "确认填入请输入 y："
    # ).strip().lower()

    # if confirm != "y":
    #     print("已经取消填入。")
    #     return False

    # 1. 复制到Windows剪贴板
    pyperclip.copy(message)

    print("[剪贴板] 文案已经复制到Windows剪贴板。")

    # 2. 使用ADB点击手机里的微信输入框
    image = base.capture_screen()
    height, width = image.shape[:2]

    input_x = int(width * 0.45)
    input_y = int(height * 0.60)

    print(
        f"[输入框] 点击手机坐标："
        f"({input_x}, {input_y})"
    )

    base.tap_xy(
        input_x,
        input_y,
        wait_after=1.0,
    )

    # 3. 找到并激活scrcpy窗口
    windows = []

    for title_keyword in [
        "LE2100",
        "OnePlus",
        "scrcpy",
    ]:
        windows = gw.getWindowsWithTitle(
            title_keyword
        )

        if windows:
            break

    if not windows:
        print(
            "没有找到scrcpy窗口。"
            "文案已经复制，请点击scrcpy窗口后手动按Ctrl+V。"
        )
        return False

    scrcpy_window = windows[0]

    try:
        if scrcpy_window.isMinimized:
            scrcpy_window.restore()
            time.sleep(0.5)

        scrcpy_window.activate()
        time.sleep(0.8)

    except Exception as exc:
        print(
            f"激活scrcpy窗口失败：{exc}"
        )

        print(
            "文案已经复制，请点击scrcpy窗口后手动按Ctrl+V。"
        )

        return False

# 4. 向scrcpy窗口发送Ctrl+V (粘贴文字)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(1.5)
    print("[粘贴完成] 文案已经粘贴进微信输入框。")

    # 识别输入框右侧绿色“发送”按钮
    send_image = base.capture_screen()

    send_point = find_green_send_button(
        send_image
    )

    if send_point is None:
        print(
            "[发送按钮] 没有识别到绿色发送按钮，"
            "请确认文案是否真正进入输入框。"
        )

        print(
            "请在手机上人工点击发送。"
        )

        return True

    send_x, send_y = send_point

    print(
        f"[发送按钮] 已识别到绿色发送按钮："
        f"({send_x}, {send_y})"
    )

    # confirm = input(
    #     "请核对联系人和文案，"
    #     "确认发送请输入 y："
    # ).strip().lower()

    # if confirm != "y":
    #     print(
    #         "没有点击发送，"
    #         "文案保留在输入框中。"
    #     )

    #     return True

    base.tap_xy(
        send_x,
        send_y,
        wait_after=1.2,
    )

    print(
        "已经点击输入框右侧的第一次发送按钮，"
        "正在等待二次确认弹窗。"
    )

    # 等待弹窗，输入y后才点击最终发送
    return confirm_and_click_final_send()


def get_current_contact_name_from_compose_page() -> str:
    """
    通过 OCR 识别编辑页面的联系人名称（如“你将发消息给 1 个朋友: Scarlett”）
    """
    image = base.capture_screen()
    # 识别顶部文案区域
    lines = ocr_lines(image, region=(0.0, 0.05, 1.0, 0.2))
    full_text = "".join([l.text for l in lines])
    
    # 正则提取名字：匹配冒号后的内容
    match = re.search(r":\s*(.+)", full_text)
    if match:
        return match.group(1).strip()
    return "unknown_contact"


MESSAGE_TEXT = "你好，我们是自橙一派影视公司"


def handle_wechat_main() -> bool:
    """
    微信主界面 -> 点击“我”
    """
    image = base.capture_screen()
    page_result = base.classify_page(image)

    if page_result.nav is None:
        print("[微信主界面] 没有识别到底部导航栏。")
        time.sleep(1.2)
        return True

    me_x, me_y = page_result.nav.centers[3]

    print(
        f"[微信主界面] 点击底部“我”："
        f"({me_x}, {me_y})"
    )

    base.tap_xy(
        me_x,
        me_y,
        wait_after=1.5,
    )

    return True


def handle_me_page() -> bool:
    """
    我 -> 设置
    """
    if not click_text_by_screenshot(
        "设置",
        region=(0.0, 0.55, 1.0, 0.92),
        wait_after=1.5,
    ):
        print("[我页面] 没有识别到“设置”。")
        time.sleep(1.2)

    return True


def handle_settings_page() -> bool:
    """
    设置 -> 其他功能
    """
    if not scroll_and_click(
        "其他功能",
        max_swipes=8,
        region=(0.0, 0.14, 1.0, 0.90),
    ):
        print("[设置页面] 没有找到“其他功能”。")
        return False

    return True


def handle_other_features_page() -> bool:
    """
    其他功能 -> 辅助功能
    """
    if not click_text_by_screenshot(
        "辅助功能",
        region=(0.0, 0.14, 1.0, 0.75),
        wait_after=1.5,
    ):
        print("[其他功能页面] 没有识别到“辅助功能”。")
        return False

    return True


def handle_accessibility_page() -> bool:
    """
    辅助功能 -> 群发助手
    """
    if not scroll_and_click(
        "群发助手",
        max_swipes=5,
        region=(0.0, 0.12, 1.0, 0.90),
    ):
        print("[辅助功能页面] 没有找到“群发助手”。")
        return False

    return True


def handle_broadcast_detail_page() -> bool:
    """
    群发助手详情页 -> 开始群发
    """
    if not click_text_by_screenshot(
        "开始群发",
        region=(0.0, 0.22, 1.0, 0.72),
        wait_after=1.8,
    ):
        print("[群发助手详情页] 没有识别到“开始群发”。")
        return False

    return True


def handle_broadcast_history_page() -> bool:
    """
    群发历史页 -> 点击最底部“新建群发”。

    不点击历史卡片中的“再发一条”。
    """
    print(
        "[群发历史页] 准备点击底部“新建群发”。"
    )

    if not click_text_by_screenshot(
        "新建群发",
        region=(0.10, 0.80, 0.90, 0.99),
        wait_after=2.0,
    ):
        print(
            "[群发历史页] 没有识别到最底部"
            "“新建群发”，不会点击“再发一条”。"
        )
        return False

    print("[群发历史页] 已点击“新建群发”。")
    return True


def handle_select_friends_page() -> bool:
    """
    选择朋友页面。

    联系人选择保留人工核对，避免重复选择或选错。
    """
    print("=" * 60)
    print("当前已经进入“选择朋友”页面。")
    print("请在手机上选择本轮联系人并点击“选中”。")
    print("=" * 60)

    input(
        "进入群发编辑页后，回到这里按回车："
    )

    if not wait_for_compose_page(
        timeout=300.0,
    ):
        print(
            "[选择朋友页面] "
            "没有确认进入群发编辑页。"
        )
        return False

    return True


def handle_compose_page() -> bool:
    """
    群发编辑页 -> 检查联系人去重 -> 填入文案。
    """
    contact_name = (
        get_current_contact_name_from_compose_page()
    )

    print(
        f"[群发编辑页] 当前识别联系人："
        f"{contact_name}"
    )

    if (
        contact_name
        and contact_name != "unknown_contact"
        and is_sent(contact_name)
    ):
        print(
            f"[重复拦截] {contact_name} "
            "已经发送过。"
        )

        input(
            "请人工核对并返回，完成后按回车："
        )
        return True

    confirm = input(
        f"准备填入文案：\n{MESSAGE_TEXT}\n"
        "确认继续请输入 y："
    ).strip().lower()

    if confirm != "y":
        print(
            "没有执行本轮发送，"
            "当前页面保持不动。"
        )
        return False

    send_success = fill_message_by_clipboard(
        MESSAGE_TEXT
    )

    if send_success:
        if (
            contact_name
            and contact_name
            != "unknown_contact"
        ):
            mark_as_sent(contact_name)

        print("[群发编辑页] 本轮发送完成。")
    else:
        print(
            "[群发编辑页] 本轮发送没有完成，"
            "不写入去重记录。"
        )

    return True


def handle_final_confirm_page() -> bool:
    """
    最终确认弹窗 -> 输入y后点击最终发送。
    """
    success = confirm_and_click_final_send()

    if success:
        print("[最终确认页] 最终发送已执行。")
    else:
        print("[最终确认页] 没有执行最终发送。")

    return True


def handle_moments_page() -> bool:
    """
    朋友圈 -> 返回一次。
    """
    print("[朋友圈页面] 返回一次。")

    base.press_back()
    time.sleep(1.2)

    return True


def handle_unknown_page() -> bool:
    """
    未知页面只等待，不直接按返回。

    微信加载期间直接返回容易把页面退错。
    """
    print(
        "[未知页面] 暂不点击、不返回，"
        "等待1.5秒后重新截图识别。"
    )

    time.sleep(1.5)
    return True


PAGE_HANDLERS = {
    "wechat_main": handle_wechat_main,
    "me": handle_me_page,
    "settings": handle_settings_page,
    "other_features": handle_other_features_page,
    "accessibility": handle_accessibility_page,
    "broadcast_detail": handle_broadcast_detail_page,
    "broadcast_history": handle_broadcast_history_page,
    "select_friends": handle_select_friends_page,
    "compose": handle_compose_page,
    "final_confirm": handle_final_confirm_page,
    "moments": handle_moments_page,
    "possible_moments": handle_moments_page,
    "unknown": handle_unknown_page,
}

def main() -> None:
    try:
        base.DEVICE_SERIAL = (
            base.detect_usb_phone()
        )

        print(
            f"已连接设备："
            f"{base.DEVICE_SERIAL}"
        )

        last_stage = ""
        same_stage_count = 0

        while True:
            # 每一轮都重新截图判断当前页面
            stage = get_current_broadcast_stage()

            print(
                f"\n{'=' * 60}\n"
                f"[页面状态机] 当前页面：{stage}\n"
                f"{'=' * 60}"
            )

            # 统计是否一直卡在同一个页面
            if stage == last_stage:
                same_stage_count += 1
            else:
                last_stage = stage
                same_stage_count = 0

            # 连续多次停留在同一个页面时暂停检查
            if same_stage_count >= 12:
                confirm = input(
                    f"连续多次停留在页面：{stage}。"
                    "输入 y 继续重试，"
                    "其他输入停止："
                ).strip().lower()

                if confirm != "y":
                    print("页面状态机已经停止。")
                    break

                same_stage_count = 0

            # 根据页面名称找到处理函数
            handler = PAGE_HANDLERS.get(
                stage
            )

            if handler is None:
                print(
                    f"[页面状态机] "
                    f"页面 {stage} 没有配置下一步。"
                )

                time.sleep(1.5)
                continue

            # 只执行当前页面的一步
            should_continue = handler()

            if not should_continue:
                print("页面状态机任务结束。")
                break

            # 等微信完成动画，再进入下一轮截图
            time.sleep(0.8)

    except KeyboardInterrupt:
        print("\n用户主动停止程序。")

    except Exception as exc:
        print(f"\n页面状态机运行出错：{exc}")

    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()