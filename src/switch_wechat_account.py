from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

# 与 comment_like.py 放在同一个 src 文件夹中。
# 直接复用朋友圈脚本的 ADB 截图、点击、返回和页面判断。
import comment_like as base


# ============================================================
# 基础配置
# ============================================================

TESSERACT_EXE = Path(
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

if TESSERACT_EXE.exists():
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_EXE)

OCR_LANG = "chi_sim+eng"

VISITED_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "visited_wechat_accounts.txt"
)


@dataclass
class OcrLine:
    text: str
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float

    @property
    def center_x(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def center_y(self) -> int:
        return (self.y1 + self.y2) // 2


@dataclass
class AccountCard:
    wechat_id: str
    nickname: str
    click_x: int
    click_y: int
    current: bool


# ============================================================
# 通用工具
# ============================================================

def normalize_text(text: str) -> str:
    return (
        text.replace(" ", "")
        .replace("\t", "")
        .replace("\n", "")
        .replace("：", ":")
        .strip()
    )


def normalize_wechat_id(wechat_id: str) -> str:
    return re.sub(
        r"[^A-Za-z0-9_-]",
        "",
        wechat_id,
    ).lower()


def looks_like_wechat_id(value: str) -> bool:
    value = normalize_wechat_id(value)

    if not 5 <= len(value) <= 32:
        return False

    if re.fullmatch(r"\d{8,20}", value):
        return True

    if re.fullmatch(r"[a-z][a-z0-9_-]{4,31}", value):
        return True

    return False


def load_visited_accounts() -> set[str]:
    if not VISITED_FILE.exists():
        return set()

    return {
        normalize_wechat_id(line)
        for line in VISITED_FILE.read_text(
            encoding="utf-8",
        ).splitlines()
        if normalize_wechat_id(line)
    }


def mark_account_visited(wechat_id: str) -> None:
    normalized = normalize_wechat_id(wechat_id)

    if not normalized:
        return

    visited = load_visited_accounts()

    if normalized in visited:
        return

    VISITED_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with VISITED_FILE.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(wechat_id.strip() + "\n")

    print(f"[账号记录] 已记录：{wechat_id}")


def crop_image(
    image: np.ndarray,
    crop_box: Optional[tuple[int, int, int, int]],
) -> tuple[np.ndarray, int, int]:
    if crop_box is None:
        return image, 0, 0

    height, width = image.shape[:2]
    x1, y1, x2, y2 = crop_box

    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(x1 + 1, min(width, x2))
    y2 = max(y1 + 1, min(height, y2))

    return image[y1:y2, x1:x2], x1, y1


def ocr_lines(
    image: np.ndarray,
    *,
    crop_box: Optional[tuple[int, int, int, int]] = None,
    scale: float = 2.0,
    psm_values: tuple[int, ...] = (6, 11),
) -> list[OcrLine]:
    """
    完全基于 base.capture_screen() 返回的截图做 OCR。

    不使用 uiautomator，不读取微信内部节点，也不保存截图文件。
    """
    crop, offset_x, offset_y = crop_image(
        image,
        crop_box,
    )

    if crop.size == 0:
        return []

    resized = cv2.resize(
        crop,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    gray = cv2.cvtColor(
        resized,
        cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.normalize(
        gray,
        None,
        0,
        255,
        cv2.NORM_MINMAX,
    )

    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        9,
    )

    variants = [gray, binary]
    all_lines: list[OcrLine] = []

    for variant in variants:
        for psm in psm_values:
            data = pytesseract.image_to_data(
                variant,
                lang=OCR_LANG,
                config=f"--psm {psm}",
                output_type=Output.DICT,
            )

            grouped: dict[
                tuple[int, int, int],
                list[int],
            ] = {}

            item_count = len(data.get("text", []))

            for index in range(item_count):
                text = str(data["text"][index]).strip()

                try:
                    confidence = float(data["conf"][index])
                except (TypeError, ValueError):
                    confidence = -1.0

                if not text or confidence < 15:
                    continue

                key = (
                    int(data["block_num"][index]),
                    int(data["par_num"][index]),
                    int(data["line_num"][index]),
                )

                grouped.setdefault(key, []).append(index)

            for indices in grouped.values():
                indices.sort(
                    key=lambda item: int(data["left"][item])
                )

                words = [
                    str(data["text"][item]).strip()
                    for item in indices
                    if str(data["text"][item]).strip()
                ]

                if not words:
                    continue

                text = "".join(words)

                left = min(
                    int(data["left"][item])
                    for item in indices
                )
                top = min(
                    int(data["top"][item])
                    for item in indices
                )
                right = max(
                    int(data["left"][item])
                    + int(data["width"][item])
                    for item in indices
                )
                bottom = max(
                    int(data["top"][item])
                    + int(data["height"][item])
                    for item in indices
                )

                confidences = []
                for item in indices:
                    try:
                        value = float(data["conf"][item])
                    except (TypeError, ValueError):
                        continue

                    if value >= 0:
                        confidences.append(value)

                average_confidence = (
                    sum(confidences) / len(confidences)
                    if confidences
                    else 0.0
                )

                all_lines.append(
                    OcrLine(
                        text=text,
                        x1=offset_x + int(left / scale),
                        y1=offset_y + int(top / scale),
                        x2=offset_x + int(right / scale),
                        y2=offset_y + int(bottom / scale),
                        confidence=average_confidence,
                    )
                )

    # 合并不同OCR方案得到的重复结果
    deduplicated: list[OcrLine] = []

    for line in sorted(
        all_lines,
        key=lambda item: (
            item.y1,
            item.x1,
            -item.confidence,
        ),
    ):
        normalized = normalize_text(line.text)

        if not normalized:
            continue

        duplicate = False

        for old in deduplicated:
            if normalize_text(old.text) != normalized:
                continue

            if (
                abs(old.center_x - line.center_x) <= 35
                and abs(old.center_y - line.center_y) <= 25
            ):
                duplicate = True

                if line.confidence > old.confidence:
                    old.x1 = line.x1
                    old.y1 = line.y1
                    old.x2 = line.x2
                    old.y2 = line.y2
                    old.confidence = line.confidence

                break

        if not duplicate:
            deduplicated.append(line)

    return deduplicated


def print_ocr_lines(
    title: str,
    lines: list[OcrLine],
) -> None:
    print(title)

    for line in lines:
        print(
            f"  y={line.center_y:4d} "
            f"conf={line.confidence:5.1f} "
            f"text={line.text}"
        )


def find_text_line(
    image: np.ndarray,
    target_text: str,
    *,
    crop_box: Optional[tuple[int, int, int, int]] = None,
    prefer_lower: bool = False,
) -> Optional[OcrLine]:
    lines = ocr_lines(
        image,
        crop_box=crop_box,
    )

    normalized_target = normalize_text(
        target_text
    )

    matches = [
        line
        for line in lines
        if normalized_target
        in normalize_text(line.text)
    ]

    if not matches:
        return None

    if prefer_lower:
        return max(
            matches,
            key=lambda item: item.center_y,
        )

    return max(
        matches,
        key=lambda item: item.confidence,
    )


def swipe_up(
    *,
    duration_ms: int = 750,
) -> None:
    image = base.capture_screen()
    height, width = image.shape[:2]

    start_x = width // 2
    start_y = int(height * 0.82)
    end_y = int(height * 0.27)

    print(
        f"[页面滑动] "
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

    time.sleep(1.2)


# ============================================================
# 进入“我”页面并提取当前微信号
# ============================================================

def ensure_me_page() -> np.ndarray:
    """
    进入微信“我”页面。

    对unknown页面先等待，不立即返回；
    只有连续多次unknown后，才谨慎返回一次。
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
                f"[截图定位] 点击“我”："
                f"({me_x}, {me_y})"
            )

            base.tap_xy(
                me_x,
                me_y,
                wait_after=2.0,
            )

            unknown_count = 0
            continue

        # unknown先等待，不要马上返回
        unknown_count += 1

        print(
            f"当前页面暂未识别，"
            f"连续 unknown={unknown_count}，"
            "先等待，不立即返回。"
        )

        if unknown_count < 4:
            time.sleep(1.5)
            continue

        # 连续4次仍无法识别，才尝试返回一次
        print(
            "连续多次未识别，"
            "现在只返回一次后重新判断。"
        )

        base.press_back()
        unknown_count = 0

    raise RuntimeError(
        "多次截图判断后仍未进入微信“我”页面"
    )

def extract_current_wechat_id(
    image: Optional[np.ndarray] = None,
) -> str:
    if image is None:
        image = base.capture_screen()

    height, width = image.shape[:2]

    profile_box = (
        int(width * 0.16),
        int(height * 0.09),
        int(width * 0.97),
        int(height * 0.34),
    )

    lines = ocr_lines(
        image,
        crop_box=profile_box,
        scale=2.5,
        psm_values=(6, 11),
    )

    print_ocr_lines(
        "[截图OCR] “我”页面个人资料区域：",
        lines,
    )

    # 第一优先：同一行中直接识别“微信号：xxxx”
    patterns = [
        r"微信号[:：]?([A-Za-z0-9_-]{5,32})",
        r"WeChatID[:：]?([A-Za-z0-9_-]{5,32})",
    ]

    for line in lines:
        text = normalize_text(line.text)

        for pattern in patterns:
            match = re.search(
                pattern,
                text,
                flags=re.IGNORECASE,
            )

            if match:
                wechat_id = match.group(1)
                print(
                    f"[截图识别] 当前微信号："
                    f"{wechat_id}"
                )
                return wechat_id

    # 第二优先：标签和微信号被OCR拆到相邻行
    label_lines = [
        line
        for line in lines
        if "微信号" in normalize_text(line.text)
    ]

    for label in label_lines:
        nearby_candidates = []

        for line in lines:
            tokens = re.findall(
                r"[A-Za-z0-9_-]{5,32}",
                normalize_text(line.text),
            )

            for token in tokens:
                if not looks_like_wechat_id(token):
                    continue

                distance = abs(
                    line.center_y - label.center_y
                )

                if distance <= int(height * 0.055):
                    nearby_candidates.append(
                        (distance, token)
                    )

        if nearby_candidates:
            nearby_candidates.sort(
                key=lambda item: item[0]
            )

            wechat_id = nearby_candidates[0][1]

            print(
                f"[截图识别] 当前微信号："
                f"{wechat_id}"
            )

            return wechat_id

    # 第三优先：从资料区所有ASCII候选中评分
    candidates: list[
        tuple[float, str]
    ] = []

    excluded = {
        "wechat",
        "wechatid",
        "status",
        "service",
        "settings",
    }

    for line in lines:
        tokens = re.findall(
            r"[A-Za-z0-9_-]{5,32}",
            normalize_text(line.text),
        )

        for token in tokens:
            normalized = normalize_wechat_id(token)

            if not looks_like_wechat_id(token):
                continue

            if normalized in excluded:
                continue

            score = 0.0

            if any(char.isdigit() for char in token):
                score += 4.0

            if "_" in token or "-" in token:
                score += 2.0

            if len(token) >= 8:
                score += 2.0

            # 微信号通常位于资料区域的下半部分
            if line.center_y >= int(height * 0.18):
                score += 2.0

            score += min(
                1.5,
                line.confidence / 100.0,
            )

            candidates.append(
                (score, token)
            )

    if candidates:
        candidates.sort(
            key=lambda item: (
                -item[0],
                -len(item[1]),
            )
        )

        wechat_id = candidates[0][1]

        print(
            "[截图识别] 未完整识别“微信号”标签，"
            f"使用最可信候选：{wechat_id}"
        )

        return wechat_id

    raise RuntimeError(
        "截图OCR没有识别到当前微信号。"
        "请查看上方打印的OCR结果。"
    )


# ============================================================
# 截图识别“设置”和“切换账号”
# ============================================================

def click_settings_from_screenshot() -> None:
    image = base.capture_screen()
    height, width = image.shape[:2]

    settings_line = find_text_line(
        image,
        "设置",
        crop_box=(
            0,
            int(height * 0.52),
            width,
            int(height * 0.94),
        ),
        prefer_lower=True,
    )

    if settings_line is not None:
        click_x = width // 2
        click_y = settings_line.center_y

        print(
            f"[截图OCR] 点击“设置”："
            f"({click_x}, {click_y})"
        )

        base.tap_xy(
            click_x,
            click_y,
            wait_after=1.8,
        )

        return

    # OCR兜底：识别“我”页面左侧蓝色齿轮图标
    roi_x1 = 0
    roi_x2 = int(width * 0.22)
    roi_y1 = int(height * 0.58)
    roi_y2 = int(height * 0.90)

    roi = image[
        roi_y1:roi_y2,
        roi_x1:roi_x2,
    ]

    hsv = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2HSV,
    )

    blue_mask = cv2.inRange(
        hsv,
        np.array([85, 80, 80], dtype=np.uint8),
        np.array([125, 255, 255], dtype=np.uint8),
    )

    contours, _ = cv2.findContours(
        blue_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    icon_candidates = []

    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(
            contour
        )
        area = cv2.contourArea(contour)

        if area < 30:
            continue

        if box_width > width * 0.15:
            continue

        if box_height > height * 0.08:
            continue

        icon_candidates.append(
            (
                area,
                roi_x1 + x + box_width // 2,
                roi_y1 + y + box_height // 2,
            )
        )

    if icon_candidates:
        _, _, icon_y = max(
            icon_candidates,
            key=lambda item: item[0],
        )

        print(
            f"[截图颜色识别] 点击设置行："
            f"({width // 2}, {icon_y})"
        )

        base.tap_xy(
            width // 2,
            icon_y,
            wait_after=1.8,
        )

        return

    raise RuntimeError(
        "截图中没有识别到“设置”文字或蓝色齿轮图标"
    )


def open_switch_account_page() -> str:
    me_image = ensure_me_page()

    current_wechat_id = (
        extract_current_wechat_id(me_image)
    )

    print(
        f"[当前账号] 微信号："
        f"{current_wechat_id}"
    )

    # 当前账号已经进入过，先记录
    mark_account_visited(
        current_wechat_id
    )

    click_settings_from_screenshot()

    for attempt in range(8):
        image = base.capture_screen()
        height, width = image.shape[:2]

        switch_line = find_text_line(
            image,
            "切换账号",
            crop_box=(
                0,
                int(height * 0.35),
                width,
                int(height * 0.90),
            ),
            prefer_lower=True,
        )

        if switch_line is not None:
            click_x = width // 2
            click_y = switch_line.center_y

            print(
                f"[截图OCR] 点击“切换账号”："
                f"({click_x}, {click_y})"
            )

            base.tap_xy(
                click_x,
                click_y,
                wait_after=2.5,
            )

            page_image = base.capture_screen()
            lines = ocr_lines(
                page_image,
                crop_box=(
                    0,
                    0,
                    page_image.shape[1],
                    int(page_image.shape[0] * 0.42),
                ),
            )

            header_text = "".join(
                normalize_text(line.text)
                for line in lines
            )

            if (
                "管理" in header_text
                or "切换账号" in header_text
                or "轻触头像" in header_text
            ):
                print("已经进入账号管理页面")
                return current_wechat_id

            # 即使标题OCR失败，也先继续到账号卡片识别阶段
            print(
                "账号管理页标题OCR不完整，"
                "继续识别账号卡片。"
            )

            return current_wechat_id

        print(
            f"当前截图未识别到“切换账号”，"
            f"向上滑动：{attempt + 1}/8"
        )

        swipe_up()

    raise RuntimeError(
        "设置页面连续滑动后仍未识别到“切换账号”"
    )


# ============================================================
# 截图OCR识别账号卡片
# ============================================================

def extract_account_cards_from_screenshot(
    current_wechat_id: str,
) -> list[AccountCard]:
    image = base.capture_screen()
    height, width = image.shape[:2]

    lines = ocr_lines(
        image,
        crop_box=(
            0,
            int(height * 0.14),
            width,
            int(height * 0.94),
        ),
        scale=2.2,
        psm_values=(6, 11),
    )

    print_ocr_lines(
        "[截图OCR] 账号管理页：",
        lines,
    )

    current_mark_lines = [
        line
        for line in lines
        if "当前使用" in normalize_text(line.text)
    ]

    found: dict[str, AccountCard] = {}

    for line in lines:
        normalized_line = normalize_text(
            line.text
        )

        tokens = re.findall(
            r"[A-Za-z0-9_-]{5,32}",
            normalized_line,
        )

        for token in tokens:
            if not looks_like_wechat_id(token):
                continue

            normalized_id = normalize_wechat_id(
                token
            )

            if not normalized_id:
                continue

            if normalized_id in found:
                continue

            # 微信号第二行位于账号卡片中，直接点击该行附近即可
            click_x = width // 2
            click_y = max(
                int(height * 0.18),
                line.center_y - int(height * 0.012),
            )

            nickname = ""
            possible_names = []

            for other in lines:
                if other is line:
                    continue

                vertical_distance = (
                    line.center_y - other.center_y
                )

                if not (
                    0
                    < vertical_distance
                    < int(height * 0.055)
                ):
                    continue

                other_text = normalize_text(
                    other.text
                )

                if not other_text:
                    continue

                if "当前使用" in other_text:
                    continue

                if looks_like_wechat_id(
                    other_text
                ):
                    continue

                possible_names.append(
                    (
                        vertical_distance,
                        other.text.strip(),
                    )
                )

            if possible_names:
                possible_names.sort(
                    key=lambda item: item[0]
                )
                nickname = possible_names[0][1]

            current = (
                normalized_id
                == normalize_wechat_id(
                    current_wechat_id
                )
            )

            if not current:
                current = any(
                    abs(
                        mark.center_y
                        - line.center_y
                    )
                    <= int(height * 0.055)
                    for mark in current_mark_lines
                )

            found[normalized_id] = AccountCard(
                wechat_id=token,
                nickname=nickname,
                click_x=click_x,
                click_y=click_y,
                current=current,
            )

    accounts = sorted(
        found.values(),
        key=lambda item: item.click_y,
    )

    return accounts

def wait_for_account_switch_complete(
    timeout: float = 60.0,
    interval: float = 1.5,
) -> np.ndarray:
    """
    等待微信账号切换完成。

    账号切换期间页面可能长时间显示空白、加载页或过渡页，
    classify_page 会返回 unknown。

    此阶段：
    - 只截图判断；
    - 不按返回键；
    - 不重新点击；
    - 最多等待 timeout 秒。
    """
    print(
        f"[切换等待] 开始等待新账号加载，"
        f"最长 {int(timeout)} 秒。"
    )

    start_time = time.time()
    attempt = 0
    latest_image = base.capture_screen()

    valid_pages = {
        "wechat_home",
        "contacts",
        "discover",
        "me",
        "wechat_main_unknown_tab",
        "moments",
        "possible_moments",
    }

    while time.time() - start_time < timeout:
        attempt += 1

        time.sleep(interval)

        latest_image = base.capture_screen()
        result = base.classify_page(
            latest_image
        )

        elapsed = time.time() - start_time

        print(
            f"[切换等待] 第 {attempt} 次检查，"
            f"已等待 {elapsed:.1f} 秒，"
            f"当前页面={result.page}"
        )

        if result.page in valid_pages:
            print(
                "[切换等待] 已检测到微信页面加载完成："
                f"{result.page}"
            )

            # 页面刚出现时再留一点稳定时间
            time.sleep(2.5)

            return base.capture_screen()

        # unknown期间绝不能按返回键
        print(
            "[切换等待] 当前仍是加载或过渡页面，"
            "继续等待，不执行返回键。"
        )

    raise RuntimeError(
        f"等待账号切换超过 {int(timeout)} 秒，"
        "仍未检测到微信主页面。"
    )

def switch_to_next_unvisited_account() -> bool:
    current_wechat_id = open_switch_account_page()
    accounts = extract_account_cards_from_screenshot(current_wechat_id)

    if not accounts:
        raise RuntimeError("账号管理页截图中没有OCR识别到微信号")

    visited = load_visited_accounts()
    
    # 筛选未进入过的候选账号
    candidates = [
        acc for acc in accounts 
        if not acc.current and normalize_wechat_id(acc.wechat_id) not in visited
    ]

    if not candidates:
        print("\n当前列表中的账号都已经进入过，本次不执行切换。")
        return False

    # 自动选择第一个未访问账号，无需人工确认
    target = candidates[0]
    print(f"\n[自动切换] 目标账号：{target.nickname or target.wechat_id} (ID: {target.wechat_id})")

    base.tap_xy(
        target.click_x,
        target.click_y,

        # 点击后只短暂等待；
        # 后面由专门函数持续判断切换是否完成
        wait_after=1.5,
    )

    # 账号切换期间只截图等待，绝不按返回键
    wait_for_account_switch_complete(
        timeout=60.0,
        interval=1.5,
    )

    # 确认切换加载完成后，再进入“我”页面验证账号
    new_me_image = ensure_me_page()
    new_wechat_id = extract_current_wechat_id(new_me_image)

    if normalize_wechat_id(new_wechat_id) != normalize_wechat_id(target.wechat_id):
        raise RuntimeError(f"账号切换失败：预期={target.wechat_id}，实际={new_wechat_id}")

    mark_account_visited(new_wechat_id)
    print(f"账号切换成功：{new_wechat_id}")
    return True


# ============================================================
# 主入口：每次只切换一个未进入账号
# ============================================================

def main() -> None:
    try:
        base.DEVICE_SERIAL = base.detect_usb_phone()
        print(f"已连接USB真机：{base.DEVICE_SERIAL}")

        # 循环：不断切换账号，直到没有未访问账号
        while True:
            print("\n--- 开始执行全自动账号切换 ---")
            success = switch_to_next_unvisited_account()
            
            if not success:
                print("所有账号已全部切换完成或无需切换。")
                break
            
            # 切换成功后，可选择在此处加入你的点赞任务逻辑
            # run_your_like_task() 
            
            time.sleep(2.0)

    except KeyboardInterrupt:
        print("\n用户主动停止程序。")
    except Exception as exc:
        print(f"\n账号切换出错：{exc}")


if __name__ == "__main__":
    main()
