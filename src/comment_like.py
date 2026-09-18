from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import hashlib

import sqlite3
import cv2
import numpy as np


# ============================================================
# 01_enter_moments_screenshot.py
#
# 目标：
# 1. 自动识别USB安卓真机，排除MuMu模拟器
# 2. 打开微信
# 3. 每次操作前截图判断当前界面
# 4. 截图定位底部导航栏中的“发现”
# 5. 截图定位发现页第一项“朋友圈”
# 6. 点击后再次截图验证是否进入朋友圈
#
# 本文件只测试“打开微信 -> 发现 -> 朋友圈”。
# 暂时不执行点赞。
# ============================================================

SCRCPY_DIR = Path(
    r"C:\Users\Deple\Desktop\scrcpy-win64-v4.0\scrcpy-win64-v4.0"
)

ADB_EXE = SCRCPY_DIR / "adb.exe"
ADB_PORT = 5038
WECHAT_PACKAGE = "com.tencent.mm"

DEVICE_SERIAL: Optional[str] = None


# 严格去重数据库
DEDUP_DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "wechat_strict_dedup.db"
)

@dataclass
class BottomNav:
    top: int
    bottom: int
    selected_index: Optional[int]
    centers: list[tuple[int, int]]
    green_scores: list[int]


@dataclass
class PageResult:
    page: str
    confidence: float
    nav: Optional[BottomNav]
    reason: str


def adb_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["ADB"] = str(ADB_EXE)
    env["ADB_SERVER_SOCKET"] = f"tcp:localhost:{ADB_PORT}"
    return env


def run_adb(
    args: list[str],
    *,
    binary: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    command = [str(ADB_EXE), "-P", str(ADB_PORT), *args]

    return subprocess.run(
        command,
        cwd=str(SCRCPY_DIR),
        env=adb_environment(),
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        timeout=timeout,
        check=False,
    )

def init_dedup_db() -> None:
    """
    初始化严格去重数据库。

    author_key：
        发布者视觉指纹，目前由头像、昵称区域生成。
    post_phash：
        朋友圈内容感知哈希。
    status：
        liked          本程序确认点赞成功
        already_liked  打开菜单时发现以前已经点赞
    """
    DEDUP_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_key TEXT NOT NULL,
                post_phash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
                    DEFAULT (datetime('now', 'localtime'))
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dedup_author
            ON dedup_records(author_key)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dedup_post
            ON dedup_records(post_phash)
            """
        )

        conn.commit()

def image_phash(image: np.ndarray) -> str:
    """
    生成64位感知哈希。

    相比普通SHA256：
    - 截图有轻微位移仍可能识别为同一内容；
    - 亮度、压缩发生小变化时更加稳定。
    """
    if image is None or image.size == 0:
        return ""

    if len(image.shape) == 3:
        gray = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2GRAY,
        )
    else:
        gray = image.copy()

    resized = cv2.resize(
        gray,
        (32, 32),
        interpolation=cv2.INTER_AREA,
    )

    dct = cv2.dct(
        np.float32(resized),
    )

    low_frequency = dct[:8, :8]
    values = low_frequency.flatten()

    # 不使用第一个直流分量计算中位数
    median = np.median(values[1:])
    bits = values > median

    number = 0

    for bit in bits:
        number = (
            number << 1
        ) | int(bool(bit))

    return f"{number:016x}"

def phash_distance(
    hash_a: str,
    hash_b: str,
) -> int:
    """
    计算两个感知哈希之间的汉明距离。

    数值越小，图片越相似。
    """
    if not hash_a or not hash_b:
        return 999

    try:
        value_a = int(hash_a, 16)
        value_b = int(hash_b, 16)
    except ValueError:
        return 999

    return (
        value_a ^ value_b
    ).bit_count()

def make_post_fingerprint(
    image: np.ndarray,
    target: tuple[
        int,
        int,
        tuple[int, int, int, int],
    ],
) -> str:
    """
    根据当前“··”按钮位置截取对应朋友圈内容，
    生成帖子内容感知哈希。
    """
    _, more_y, _ = target
    height, width = image.shape[:2]

    crop_x1 = int(width * 0.10)
    crop_x2 = int(width * 0.93)

    crop_y1 = max(
        int(height * 0.06),
        more_y - int(height * 0.48),
    )

    crop_y2 = max(
        crop_y1 + 30,
        more_y - int(height * 0.025),
    )

    crop_y2 = min(
        height,
        crop_y2,
    )

    crop = image[
        crop_y1:crop_y2,
        crop_x1:crop_x2,
    ]

    if crop.size == 0:
        return ""

    return image_phash(crop)

def make_author_fingerprint(
    image: np.ndarray,
    target: tuple[
        int,
        int,
        tuple[int, int, int, int],
    ],
) -> str:
    """
    根据当前朋友圈发布者区域生成视觉发布者指纹。

    当前使用：
    - 左侧头像区域
    - 昵称所在区域

    注意：
    这不是微信后台真实ID，而是视觉近似ID。
    后面进入资料页读取微信号后，可以替换为真实ID。
    """
    _, more_y, _ = target
    height, width = image.shape[:2]

    # 发布者通常位于“··”按钮上方区域左侧
    crop_y1 = max(
        int(height * 0.06),
        more_y - int(height * 0.50),
    )

    crop_y2 = max(
        crop_y1 + 30,
        more_y - int(height * 0.23),
    )

    crop_y2 = min(
        height,
        crop_y2,
    )

    crop_x1 = 0
    crop_x2 = int(width * 0.38)

    author_crop = image[
        crop_y1:crop_y2,
        crop_x1:crop_x2,
    ]

    if author_crop.size == 0:
        return ""

    return image_phash(author_crop)

def should_skip_strict(
    author_key: str,
    post_phash: str,
    author_threshold: int = 12,
    post_threshold: int = 8,
) -> tuple[bool, str]:
    """
    严格去重模式：

    1. 发布者视觉指纹相似 -> 跳过
    2. 帖子内容指纹相似 -> 跳过
    3. 两项都不相似 -> 继续检查点赞状态

    感知哈希不能使用字符串完全相等判断，
    必须使用汉明距离。
    """
    if not author_key:
        return True, "发布者指纹为空"

    if not post_phash:
        return True, "帖子内容指纹为空"

    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT author_key, post_phash
            FROM dedup_records
            """
        ).fetchall()

    nearest_author_distance = 999
    nearest_post_distance = 999

    # 第一层：发布者近似去重
    for old_author_key, _ in rows:
        if not old_author_key:
            continue

        distance = phash_distance(
            author_key,
            old_author_key,
        )

        nearest_author_distance = min(
            nearest_author_distance,
            distance,
        )

        if distance <= author_threshold:
            return (
                True,
                "该发布者已经处理过，"
                f"发布者指纹距离={distance}，"
                f"阈值={author_threshold}",
            )

    # 第二层：帖子内容近似去重
    for _, old_post_phash in rows:
        if not old_post_phash:
            continue

        distance = phash_distance(
            post_phash,
            old_post_phash,
        )

        nearest_post_distance = min(
            nearest_post_distance,
            distance,
        )

        if distance <= post_threshold:
            return (
                True,
                "该朋友圈内容已经处理过，"
                f"帖子指纹距离={distance}，"
                f"阈值={post_threshold}",
            )

    print(
        "去重距离检查："
        f"最近发布者距离={nearest_author_distance}，"
        f"最近帖子距离={nearest_post_distance}"
    )

    return False, ""

def save_dedup_record(
    author_key: str,
    post_phash: str,
    status: str,
) -> None:
    """
    只有两种情况允许写数据库：

    1. 点赞确认成功；
    2. 明确发现这条以前已经点赞。

    unknown、点击失败、识别失败不能写入。
    """
    if status not in {
        "liked",
        "already_liked",
    }:
        raise ValueError(
            f"不允许保存的状态：{status}"
        )

    if not author_key or not post_phash:
        return

    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        exists = conn.execute(
            """
            SELECT 1
            FROM dedup_records
            WHERE author_key = ?
              AND post_phash = ?
            LIMIT 1
            """,
            (
                author_key,
                post_phash,
            ),
        ).fetchone()

        if exists:
            return

        conn.execute(
            """
            INSERT INTO dedup_records (
                author_key,
                post_phash,
                status
            )
            VALUES (?, ?, ?)
            """,
            (
                author_key,
                post_phash,
                status,
            ),
        )

        conn.commit()

def detect_usb_phone() -> str:
    start = run_adb(["start-server"])
    if start.returncode != 0:
        raise RuntimeError(start.stderr or start.stdout or "ADB服务启动失败")

    result = run_adb(["devices", "-l"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "读取设备失败")

    candidates: list[str] = []

    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("List of devices"):
            continue

        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue

        serial = parts[0]

        # 排除MuMu、安卓模拟器和TCP设备
        if serial.startswith("emulator-"):
            continue
        if ":" in serial:
            continue

        candidates.append(serial)

    if not candidates:
        raise RuntimeError(
            "没有找到已授权的USB安卓真机。\n"
            f"当前ADB输出：\n{result.stdout}"
        )

    if len(candidates) == 1:
        return candidates[0]

    print("检测到多台USB真机：")
    for index, serial in enumerate(candidates, start=1):
        print(f"{index}. {serial}")

    raw = input("请选择设备序号：").strip()

    try:
        return candidates[int(raw) - 1]
    except (ValueError, IndexError):
        raise RuntimeError("设备序号无效")


def run_device_adb(
    args: list[str],
    *,
    binary: bool = False,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    if not DEVICE_SERIAL:
        raise RuntimeError("设备尚未初始化")

    return run_adb(
        ["-s", DEVICE_SERIAL, *args],
        binary=binary,
        timeout=timeout,
    )


def open_wechat() -> None:
    result = run_device_adb(
        [
            "shell",
            "am",
            "start",
            "-n",
            "com.tencent.mm/.ui.LauncherUI",
        ]
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "微信启动失败")

    time.sleep(3.0)


def press_back() -> None:
    run_device_adb(["shell", "input", "keyevent", "4"])
    time.sleep(1.2)


def tap_xy(x: int, y: int, wait_after: float = 1.5) -> None:
    result = run_device_adb(
        ["shell", "input", "tap", str(int(x)), str(int(y))]
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "点击失败")

    time.sleep(wait_after)


def capture_screen(_filename: str = "") -> np.ndarray:
    """
    截图只保存在内存中，不写入硬盘。
    参数仅为兼容原调用，不会创建文件。
    """
    result = run_device_adb(
        ["exec-out", "screencap", "-p"],
        binary=True,
        timeout=30,
    )

    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(stderr or "截图失败")

    array = np.frombuffer(result.stdout, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)

    if image is None:
        raise RuntimeError("截图解码失败")

    return image


def foreground_package() -> str:
    result = run_device_adb(
        ["shell", "dumpsys", "window", "windows"],
        timeout=20,
    )

    raw = f"{result.stdout}\n{result.stderr}"

    patterns = [
        r"mCurrentFocus=.*? ([A-Za-z0-9._]+)/",
        r"mFocusedApp=.*? ([A-Za-z0-9._]+)/",
    ]

    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            return match.group(1)

    return ""


def green_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # 微信绿色的宽松范围
    lower = np.array([35, 65, 55], dtype=np.uint8)
    upper = np.array([95, 255, 255], dtype=np.uint8)

    return cv2.inRange(hsv, lower, upper)


def detect_bottom_navigation(image: np.ndarray) -> Optional[BottomNav]:
    """
    从截图底部寻找微信四栏导航。

    判断依据：
    1. 位于屏幕底部；
    2. 背景以白色/浅灰色为主；
    3. 四个等宽区域中存在图标或文字暗色像素；
    4. 其中一个区域通常包含较多微信绿色像素。
    """
    height, width = image.shape[:2]

    search_top = int(height * 0.84)
    search_bottom = int(height * 0.965)
    search = image[search_top:search_bottom]

    gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)

    # 每一行浅色背景比例
    bright_ratio = (gray > 215).mean(axis=1)

    nav_top_relative: Optional[int] = None
    minimum_band = max(35, int(height * 0.055))

    for row in range(0, len(bright_ratio) - minimum_band):
        band = bright_ratio[row:row + minimum_band]

        if float(np.mean(band)) > 0.76:
            nav_top_relative = row
            break

    if nav_top_relative is None:
        return None

    nav_top = search_top + nav_top_relative
    nav_bottom = search_bottom

    # 导航区域过矮或过高都不可信
    nav_height = nav_bottom - nav_top
    if not (
        int(height * 0.075)
        <= nav_height
        <= int(height * 0.15)
    ):
        return None
    

    nav_image = image[nav_top:nav_bottom]
    nav_gray = cv2.cvtColor(nav_image, cv2.COLOR_BGR2GRAY)
    nav_green = green_mask(nav_image)

    centers: list[tuple[int, int]] = []
    green_scores: list[int] = []
    content_scores: list[int] = []

    for index in range(4):
        x1 = int(width * index / 4)
        x2 = int(width * (index + 1) / 4)

        # 略去每栏边缘，避免相邻图标互相干扰
        margin = max(3, int(width * 0.015))
        roi_gray = nav_gray[:, x1 + margin:x2 - margin]
        roi_green = nav_green[:, x1 + margin:x2 - margin]

        dark_count = int(np.count_nonzero(roi_gray < 170))
        green_count = int(np.count_nonzero(roi_green > 0))

        content_scores.append(dark_count)
        green_scores.append(green_count)
        centers.append(((x1 + x2) // 2, nav_top + nav_height // 2))

    # 四栏至少有三栏出现明显图标/文字
    active_columns = sum(score > max(25, int(width * height * 0.00008))
                         for score in content_scores)

    if active_columns < 3:
        return None

    selected_index: Optional[int] = None
    best_index = int(np.argmax(green_scores))
    best_score = green_scores[best_index]

    sorted_scores = sorted(green_scores, reverse=True)
    second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0

    # 当前栏的绿色应当明显多于其他栏
    if best_score > max(25, int(width * height * 0.000035)):
        if best_score >= max(second_score * 1.25, second_score + 12):
            selected_index = best_index

    return BottomNav(
        top=nav_top,
        bottom=nav_bottom,
        selected_index=selected_index,
        centers=centers,
        green_scores=green_scores,
    )


def find_ellipsis_candidates(
    image: np.ndarray,
) -> list[tuple[int, int, tuple[int, int, int, int]]]:
    """
    识别朋友圈右侧真正的“··”按钮。

    识别依据：
    1. 按钮位于屏幕最右侧；
    2. 外层是低饱和度的浅灰色圆角矩形；
    3. 按钮内部存在2～3个水平排列的小暗点；
    4. 排除照片内部、头像、昵称和视频画面中的暗点。
    """
    height, width = image.shape[:2]

    # 只分析屏幕最右侧区域
    roi_x1 = int(width * 0.80)
    roi_y1 = int(height * 0.12)
    roi_y2 = int(height * 0.965)

    roi = image[roi_y1:roi_y2, roi_x1:width]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    # 浅灰色按钮背景：
    # 饱和度低、亮度高，但不能是纯白色朋友圈背景
    gray_button_mask = (
        (saturation <= 60)
        & (value >= 215)
        & (value <= 253)
    ).astype(np.uint8) * 255

    # 填补按钮内部暗点造成的小孔
    gray_button_mask = cv2.morphologyEx(
        gray_button_mask,
        cv2.MORPH_CLOSE,
        np.ones((9, 9), np.uint8),
    )

    gray_button_mask = cv2.morphologyEx(
        gray_button_mask,
        cv2.MORPH_OPEN,
        np.ones((3, 3), np.uint8),
    )

    contours, _ = cv2.findContours(
        gray_button_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    candidates: list[
        tuple[int, int, tuple[int, int, int, int]]
    ] = []

    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)

        absolute_x = roi_x1 + x
        absolute_y = roi_y1 + y

        center_x = absolute_x + box_width // 2
        center_y = absolute_y + box_height // 2

        # 必须真正靠近屏幕右侧
        if center_x < int(width * 0.88):
            continue

        # 排除顶部标题栏和底部系统导航栏
        if not (
            int(height * 0.14)
            < center_y
            < int(height * 0.955)
        ):
            continue

        # 微信“··”按钮通常是一个小型横向圆角矩形
        if not (
            int(width * 0.045)
            <= box_width
            <= int(width * 0.15)
        ):
            continue

        if not (
            int(height * 0.015)
            <= box_height
            <= int(height * 0.060)
        ):
            continue

        aspect_ratio = box_width / max(box_height, 1)

        if not (1.10 <= aspect_ratio <= 3.80):
            continue

        patch = image[
            absolute_y:absolute_y + box_height,
            absolute_x:absolute_x + box_width,
        ]

        if patch.size == 0:
            continue

        patch_hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        patch_gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)

        low_saturation_ratio = float(
            np.mean(patch_hsv[:, :, 1] <= 65)
        )

        light_background_ratio = float(
            np.mean(patch_gray >= 205)
        )

        # 照片区域通常颜色丰富、灰度变化大
        if low_saturation_ratio < 0.68:
            continue

        if light_background_ratio < 0.62:
            continue

        # 在按钮内部识别深色小点
        inner_x1 = int(box_width * 0.12)
        inner_x2 = int(box_width * 0.88)
        inner_y1 = int(box_height * 0.15)
        inner_y2 = int(box_height * 0.85)

        inner = patch[
            inner_y1:inner_y2,
            inner_x1:inner_x2,
        ]

        if inner.size == 0:
            continue

        inner_gray = cv2.cvtColor(
            inner,
            cv2.COLOR_BGR2GRAY,
        )

        dot_mask = cv2.inRange(
            inner_gray,
            20,
            195,
        )

        dot_mask = cv2.morphologyEx(
            dot_mask,
            cv2.MORPH_OPEN,
            np.ones((2, 2), np.uint8),
        )

        count, _, stats, centers = (
            cv2.connectedComponentsWithStats(
                dot_mask,
                connectivity=8,
            )
        )

        dots: list[tuple[float, float, int, int, int]] = []

        inner_height, inner_width = inner_gray.shape

        for index in range(1, count):
            dot_x, dot_y, dot_width, dot_height, area = stats[index]
            dot_center_x, dot_center_y = centers[index]

            if not (
                2
                <= area
                <= max(90, int(inner_width * inner_height * 0.10))
            ):
                continue

            if dot_width > int(inner_width * 0.28):
                continue

            if dot_height > int(inner_height * 0.55):
                continue

            dot_aspect = dot_width / max(dot_height, 1)

            if not (0.35 <= dot_aspect <= 2.8):
                continue

            dots.append(
                (
                    dot_center_x,
                    dot_center_y,
                    dot_width,
                    dot_height,
                    area,
                )
            )

        # 寻找同一水平线上的2～3个点
        valid_dot_group = False

        for first in dots:
            same_row = [
                dot
                for dot in dots
                if abs(dot[1] - first[1])
                <= max(3, inner_height * 0.22)
            ]

            same_row.sort(key=lambda item: item[0])

            if 2 <= len(same_row) <= 4:
                x_positions = [dot[0] for dot in same_row]

                span = max(x_positions) - min(x_positions)

                if (
                    inner_width * 0.10
                    <= span
                    <= inner_width * 0.75
                ):
                    valid_dot_group = True
                    break

        if not valid_dot_group:
            continue

        candidates.append(
            (
                center_x,
                center_y,
                (
                    absolute_x,
                    absolute_y,
                    absolute_x + box_width,
                    absolute_y + box_height,
                ),
            )
        )

    # 去重并从上到下排列
    candidates.sort(key=lambda item: item[1])

    result: list[
        tuple[int, int, tuple[int, int, int, int]]
    ] = []

    for candidate in candidates:
        duplicate = any(
            abs(candidate[0] - old[0])
            < int(width * 0.04)
            and abs(candidate[1] - old[1])
            < int(height * 0.025)
            for old in result
        )

        if not duplicate:
            result.append(candidate)

    return result

def find_like_button_by_vision(image: np.ndarray, expected_y: int) -> Optional[tuple[int, int]]:
    """
    通过截图视觉特征查找“赞/评论”横条并返回“赞”的位置
    """
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # 提取深色菜单区域 (朋友圈弹出的菜单通常是深灰/黑色背景)
    # 我们通过查找大面积的深色轮廓来锁定菜单位置
    mask = cv2.inRange(hsv, np.array([0, 0, 0]), np.array([180, 255, 120]))
    
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # 筛选：菜单条宽度通常占屏幕宽度的 25% 以上，且高度在一定范围内
        if w > width * 0.25 and 60 < h < 250:
            # 必须靠近点击“三个点”后的那个高度位置
            if abs(y + h/2 - expected_y) < 200:
                # “赞”通常位于菜单条的左侧 1/4 处
                like_x = x + int(w * 0.20)
                like_y = y + h // 2
                return (like_x, like_y)
    return None

def top_cover_score(image: np.ndarray) -> float:
    """
    判断页面顶部是否存在朋友圈的大幅封面图。

    朋友圈顶部通常有一块接近全宽的大图；
    “我”页面顶部通常以白色背景和列表为主。
    """
    height, width = image.shape[:2]

    # 排除最上面的安卓状态栏
    y1 = int(height * 0.035)
    y2 = int(height * 0.40)

    roi = image[y1:y2, 0:width]

    if roi.size == 0:
        return 0.0

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    # 非白色、有颜色或较暗的像素，通常属于封面图片
    image_pixel_ratio = float(
        np.mean(
            (saturation > 35)
            | (value < 205)
        )
    )

    return image_pixel_ratio


def classify_page(image: np.ndarray) -> PageResult:
    """
    页面判断优先级：

    1. 朋友圈右侧“··”按钮；
    2. 朋友圈顶部大幅封面；
    3. 微信底部四栏导航；
    4. 未知页面。
    """

    # 第一种朋友圈特征：右侧省略号按钮
    ellipsis = find_ellipsis_candidates(image)

    if ellipsis:
        return PageResult(
            page="moments",
            confidence=0.95,
            nav=None,
            reason=(
                f"检测到朋友圈右侧省略号按钮，"
                f"共 {len(ellipsis)} 个"
            ),
        )

    # 第二种朋友圈特征：顶部大幅封面图
    # 当前首屏可能还没有露出“··”，所以必须先判断封面
    cover_score = top_cover_score(image)

    if cover_score >= 0.52:
        return PageResult(
            page="moments",
            confidence=0.88,
            nav=None,
            reason=(
                "检测到朋友圈顶部大幅封面图，"
                f"封面特征值={cover_score:.3f}"
            ),
        )

    if cover_score >= 0.38:
        return PageResult(
            page="possible_moments",
            confidence=0.68,
            nav=None,
            reason=(
                "检测到疑似朋友圈顶部封面，"
                f"封面特征值={cover_score:.3f}"
            ),
        )

    # 朋友圈特征不存在时，再判断微信主界面四栏导航
    nav = detect_bottom_navigation(image)

    if nav is not None:
        names = {
            0: "wechat_home",
            1: "contacts",
            2: "discover",
            3: "me",
        }

        if nav.selected_index is not None:
            return PageResult(
                page=names[nav.selected_index],
                confidence=0.90,
                nav=nav,
                reason=(
                    "检测到微信四栏底部导航，"
                    f"第 {nav.selected_index + 1} 栏绿色像素最多"
                ),
            )

        return PageResult(
            page="wechat_main_unknown_tab",
            confidence=0.62,
            nav=nav,
            reason="检测到微信四栏导航，但没有确定当前选中栏",
        )

    return PageResult(
        page="unknown",
        confidence=0.20,
        nav=None,
        reason=(
            "未检测到朋友圈封面、朋友圈省略号"
            "或微信底部导航"
        ),
    )


def annotate_page(
    image: np.ndarray,
    result: PageResult,
) -> np.ndarray:
    """只在内存中绘制识别结果，不保存图片。"""
    debug = image.copy()
    height, width = debug.shape[:2]

    if result.nav is not None:
        cv2.rectangle(
            debug,
            (0, result.nav.top),
            (width - 1, result.nav.bottom - 1),
            (255, 0, 0),
            3,
        )

        for index, (x, y) in enumerate(result.nav.centers):
            color = (0, 0, 255)
            if index == result.nav.selected_index:
                color = (0, 200, 0)

            cv2.circle(debug, (x, y), 18, color, -1)
            cv2.putText(
                debug,
                str(index + 1),
                (x - 10, y - 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                color,
                2,
                cv2.LINE_AA,
            )

    for x, y, box in find_ellipsis_candidates(image):
        x1, y1, x2, y2 = box
        cv2.rectangle(debug, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.circle(debug, (x, y), 8, (0, 0, 255), -1)

    cv2.rectangle(
        debug,
        (0, 0),
        (width - 1, max(70, int(height * 0.06))),
        (255, 255, 255),
        -1,
    )
    cv2.putText(
        debug,
        f"page={result.page} confidence={result.confidence:.2f}",
        (12, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )

    return debug


def find_moments_entry(
    image: np.ndarray,
    nav: BottomNav,
) -> Optional[tuple[int, int, tuple[int, int, int, int]]]:
    """
    在“发现”页截图中定位最上方的“朋友圈”入口。

    不使用固定像素：
    1. 只分析标题栏下方、底部导航上方；
    2. 在左侧寻找最靠上的彩色图标；
    3. 使用该图标的纵向中心作为第一项入口中心；
    4. 点击整行的中部位置。
    """
    height, width = image.shape[:2]

    content_top = int(height * 0.07)
    content_bottom = min(nav.top, int(height * 0.52))

    roi = image[content_top:content_bottom, 0:int(width * 0.34)]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    # 彩色图标：饱和度较高，排除黑色文字和白色背景
    mask = cv2.inRange(
        hsv,
        np.array([0, 70, 55], dtype=np.uint8),
        np.array([179, 255, 255], dtype=np.uint8),
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        np.ones((5, 5), np.uint8),
    )

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    icon_candidates: list[tuple[int, int, int, int, int]] = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = cv2.contourArea(contour)

        absolute_y = content_top + y

        if area < max(12, width * height * 0.00001):
            continue
        if w > width * 0.20 or h > height * 0.12:
            continue
        if absolute_y < height * 0.09:
            continue

        icon_candidates.append((absolute_y, x, y, w, h))

    if not icon_candidates:
        return None

    # “朋友圈”通常是发现页中最上方的彩色图标
    icon_candidates.sort(key=lambda item: item[0])
    _, x, y, w, h = icon_candidates[0]

    center_y = content_top + y + h // 2

    # 根据上下留白估计整行高度
    row_half_height = max(int(height * 0.035), h)
    y1 = max(content_top, center_y - row_half_height)
    y2 = min(nav.top - 1, center_y + row_half_height)

    # 点击整行中部，避免图标本身或右侧箭头识别误差
    click_x = width // 2
    click_y = (y1 + y2) // 2

    return click_x, click_y, (0, y1, width - 1, y2)


def annotate_moments_entry(
    image: np.ndarray,
    target: tuple[int, int, tuple[int, int, int, int]],
) -> np.ndarray:
    """只在内存中标记朋友圈入口。"""
    debug = image.copy()
    x, y, box = target
    x1, y1, x2, y2 = box

    cv2.rectangle(debug, (x1, y1), (x2, y2), (0, 0, 255), 4)
    cv2.circle(debug, (x, y), 14, (0, 0, 255), -1)
    return debug


def capture_and_classify(
    prefix: str,
) -> tuple[np.ndarray, PageResult, np.ndarray]:
    image = capture_screen(prefix)
    result = classify_page(image)
    debug_image = annotate_page(image, result)

    print(
        f"[页面判断] {result.page} "
        f"| 置信度={result.confidence:.2f} "
        f"| {result.reason}"
    )

    return image, result, debug_image


def ensure_wechat_main() -> tuple[np.ndarray, PageResult]:
    """
    微信可能停在聊天详情、设置页或其他页面。
    每按一次返回键，都先截图判断；不会连续盲目返回。
    """
    for attempt in range(5):
        image, result, _ = capture_and_classify(
            f"step_home_check_{attempt + 1}"
        )

        if result.page in {
            "wechat_home",
            "contacts",
            "discover",
            "me",
            "wechat_main_unknown_tab",
            "moments",
            "possible_moments",
        }:
            return image, result

        package = foreground_package()

        if package and package != WECHAT_PACKAGE:
            print(f"当前前台不是微信，而是：{package}，重新打开微信")
            open_wechat()
            continue

        print("当前仍在微信二级页面，只返回一次后重新截图判断")
        press_back()

    raise RuntimeError(
        "经过逐次截图判断，仍无法回到微信主界面。"
        "当前未保留截图；如需调试可临时开启保存模式。"
    )


def enter_discover(
    image: np.ndarray,
    result: PageResult,
) -> tuple[np.ndarray, PageResult]:
    if result.page == "discover":
        print("当前已经在“发现”页，不重复点击")
        return image, result

    if result.page in {"moments", "possible_moments"}:
        return image, result

    nav = result.nav

    if nav is None:
        raise RuntimeError("当前截图没有识别到微信底部导航，无法定位“发现”")

    discover_x, discover_y = nav.centers[2]

    print(f"[截图定位] “发现”点击位置：({discover_x}, {discover_y})")

    tap_xy(discover_x, discover_y, wait_after=2.0)

    new_image, new_result, _ = capture_and_classify("step_after_discover")

    if new_result.page != "discover":
        raise RuntimeError(
            "点击截图定位的“发现”后，页面验证未通过。"
            f"当前判断为：{new_result.page}"
        )

    return new_image, new_result


def enter_moments(
    image: np.ndarray,
    result: PageResult,
) -> tuple[np.ndarray, PageResult]:
    if result.page in {"moments", "possible_moments"}:
        print("当前已经处于朋友圈页面")
        return image, result

    if result.page != "discover" or result.nav is None:
        raise RuntimeError("当前不在“发现”页，无法截图定位“朋友圈”入口")

    target = find_moments_entry(image, result.nav)
    if target is None:
        raise RuntimeError("截图中没有定位到发现页顶部的彩色入口图标。")

    x, y, _ = target
    print(f"[截图定位] “朋友圈”入口：({x}, {y})")
    
    # 1. 增加点击后的深度休眠，确保转场动画完成
    tap_xy(x, y, wait_after=3.5) 

    # 2. 二次校验：如果验证失败，不要立即报错，再等 2 秒，因为朋友圈加载确实慢
    new_image, new_result, _ = capture_and_classify("step_after_moments")

    if new_result.page not in {"moments", "possible_moments"}:
        print("转场后识别失败，尝试等待加载...")
        time.sleep(2.0)
        new_image, new_result, _ = capture_and_classify("step_after_moments_retry")

    if new_result.page not in {"moments", "possible_moments"}:
        raise RuntimeError(
            "点击“朋友圈”后，页面验证未通过。"
            f"当前判断为：{new_result.page}"
        )

    return new_image, new_result


def run_navigation() -> tuple[np.ndarray, PageResult]:
    global DEVICE_SERIAL

    print("=" * 68)
    print("微信截图导航测试：判断界面 -> 发现 -> 朋友圈")
    print("=" * 68)

    try:
        DEVICE_SERIAL = detect_usb_phone()
        print(f"1. 已连接USB真机：{DEVICE_SERIAL}")

        open_wechat()
        print("2. 微信已打开")

        image, result = ensure_wechat_main()

        if result.page not in {"moments", "possible_moments"}:
            image, result = enter_discover(image, result)

        if result.page not in {"moments", "possible_moments"}:
            image, result = enter_moments(image, result)

        print("\n完成：已通过截图判断进入朋友圈。")
        return image, result

    except KeyboardInterrupt:
        raise
    except Exception:
        raise


def resize_for_display(image: np.ndarray, max_height: int = 850) -> np.ndarray:
    height, width = image.shape[:2]
    if height <= max_height:
        return image

    scale = max_height / height
    return cv2.resize(
        image,
        (int(width * scale), int(height * scale)),
        interpolation=cv2.INTER_AREA,
    )


def show_image(title: str, image: np.ndarray) -> None:
    """
    直接在OpenCV窗口显示内存图片，不创建截图文件。
    """
    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    cv2.imshow(title, resize_for_display(image))
    cv2.waitKey(1)


def close_image(title: str) -> None:
    try:
        cv2.destroyWindow(title)
    except cv2.error:
        pass


def annotate_ellipsis_candidates(
    image: np.ndarray,
    candidates: list[tuple[int, int, tuple[int, int, int, int]]],
) -> np.ndarray:
    debug = image.copy()

    for index, (_, _, box) in enumerate(candidates, start=1):
        x1, y1, x2, y2 = box
        cv2.rectangle(debug, (x1, y1), (x2, y2), (0, 0, 255), 3)
        cv2.putText(
            debug,
            str(index),
            (max(5, x1 - 35), max(30, y1 + 25)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (0, 0, 255),
            3,
            cv2.LINE_AA,
        )

    return debug


def select_candidate(
    candidates: list[tuple[int, int, tuple[int, int, int, int]]],
) -> tuple[int, int]:
    if not candidates:
        raise RuntimeError("没有识别到朋友圈右侧“··”候选按钮")

    print(f"共识别到 {len(candidates)} 个候选按钮。")

    if len(candidates) == 1:
        raw = input("按回车使用该候选；输入 n 取消：").strip().lower()
        if raw == "n":
            raise RuntimeError("用户取消")
        return candidates[0][0], candidates[0][1]

    raw = input("请查看候选编号窗口，输入要测试的编号：").strip()

    try:
        index = int(raw) - 1
        return candidates[index][0], candidates[index][1]
    except (ValueError, IndexError):
        raise RuntimeError("候选编号无效")


def find_dark_popup(
    image: np.ndarray,
    expected_x: int,
    expected_y: int,
) -> Optional[tuple[int, int, int, int]]:
    """
    在“··”按钮同一水平线附近识别“赞/评论”菜单。

    不再从整块轮廓判断菜单高度，避免菜单和上方图片、
    视频中的深色内容连在一起后被错误排除。
    """
    height, width = image.shape[:2]

    # 菜单中心通常与“··”按钮基本处于同一水平线
    band_half_height = max(
        55,
        int(height * 0.040),
    )

    roi_y1 = max(
        0,
        expected_y - band_half_height,
    )

    roi_y2 = min(
        height,
        expected_y + band_half_height,
    )

    # 菜单位于“··”左侧
    roi_x1 = int(width * 0.12)

    roi_x2 = min(
        width,
        expected_x - int(width * 0.015),
    )

    if roi_x2 <= roi_x1:
        return None

    roi = image[
        roi_y1:roi_y2,
        roi_x1:roi_x2,
    ]

    if roi.size == 0:
        return None

    gray = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2GRAY,
    )

    # 深灰菜单背景
    dark_mask = cv2.inRange(
        gray,
        0,
        190,
    )

    # 水平方向连接菜单背景、图标和文字之间的空隙
    dark_mask = cv2.morphologyEx(
        dark_mask,
        cv2.MORPH_CLOSE,
        np.ones((27, 7), np.uint8),
    )

    dark_mask = cv2.morphologyEx(
        dark_mask,
        cv2.MORPH_OPEN,
        np.ones((3, 3), np.uint8),
    )

    contours, _ = cv2.findContours(
        dark_mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    candidates: list[
        tuple[float, tuple[int, int, int, int]]
    ] = []

    for contour in contours:
        x, y, popup_width, popup_height = (
            cv2.boundingRect(contour)
        )

        absolute_x1 = roi_x1 + x
        absolute_y1 = roi_y1 + y
        absolute_x2 = absolute_x1 + popup_width
        absolute_y2 = absolute_y1 + popup_height

        center_y = (
            absolute_y1 + absolute_y2
        ) / 2

        # 菜单必须是横向长条
        if popup_width < int(width * 0.24):
            continue

        if popup_width > int(width * 0.78):
            continue

        if popup_height < int(height * 0.018):
            continue

        # 当前已经只截取了狭窄水平带，
        # 因此不再设置过小的菜单高度上限
        if popup_height > int(height * 0.095):
            continue

        if popup_width < popup_height * 2.1:
            continue

        # 菜单中心必须接近“··”按钮高度
        if abs(center_y - expected_y) > int(height * 0.045):
            continue

        # 菜单必须处于按钮左侧
        if absolute_x1 >= expected_x:
            continue

        patch = gray[
            y:y + popup_height,
            x:x + popup_width,
        ]

        if patch.size == 0:
            continue

        dark_ratio = float(
            np.mean(patch < 190)
        )

        mean_gray = float(
            np.mean(patch)
        )

        if dark_ratio < 0.26:
            continue

        if mean_gray > 195:
            continue

        right_distance = abs(
            expected_x - absolute_x2
        )

        # 菜单右侧通常距离“··”按钮比较近
        if right_distance > int(width * 0.24):
            continue

        score = (
            popup_width * 1.5
            + dark_ratio * 700
            - abs(center_y - expected_y) * 2
            - right_distance * 0.4
        )

        candidates.append(
            (
                score,
                (
                    absolute_x1,
                    absolute_y1,
                    absolute_x2,
                    absolute_y2,
                ),
            )
        )

    if not candidates:
        print(
            "[菜单识别] 同一水平线附近"
            "没有找到符合条件的深色横条"
        )
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    popup = candidates[0][1]

    print(f"[菜单识别] 找到深色菜单：{popup}")

    return popup

def wait_for_like_popup(
    expected_x: int,
    expected_y: int,
    attempts: int = 8,
) -> tuple[
    np.ndarray,
    Optional[tuple[int, int, int, int]],
]:
    """
    点击“··”后连续截图几次，等待点赞菜单出现。
    """
    latest_image = capture_screen()

    for attempt in range(attempts):
        if attempt > 0:
            time.sleep(0.25)
            latest_image = capture_screen()

        popup = find_dark_popup(
            latest_image,
            expected_x=expected_x,
            expected_y=expected_y,
        )

        if popup is not None:
            print(
                f"[菜单等待] 第 {attempt + 1} 次截图"
                "确认菜单已经出现。"
            )

            return latest_image, popup

        print(
            f"[菜单等待] 第 {attempt + 1} 次"
            "暂未识别到菜单。"
        )

    return latest_image, None

def annotate_like_area(
    image: np.ndarray,
    popup: tuple[int, int, int, int],
) -> tuple[np.ndarray, tuple[int, int]]:
    debug = image.copy()
    x1, y1, x2, y2 = popup

    popup_width = x2 - x1
    like_x = int(x1 + popup_width * 0.25)
    like_y = (y1 + y2) // 2

    cv2.rectangle(debug, (x1, y1), (x2, y2), (255, 0, 0), 3)
    cv2.rectangle(
        debug,
        (x1, y1),
        (x1 + popup_width // 2, y2),
        (0, 0, 255),
        4,
    )
    cv2.circle(debug, (like_x, like_y), 12, (0, 0, 255), -1)

    return debug, (like_x, like_y)


def filter_safe_ellipsis_candidates(
    image: np.ndarray,
    candidates: list[
        tuple[int, int, tuple[int, int, int, int]]
    ],
) -> list[
    tuple[int, int, tuple[int, int, int, int]]
]:
    """
    严格过滤朋友圈真正的“··”按钮。

    核心规则：
    1. 必须在屏幕最右侧；
    2. 允许位于屏幕底部时间行；
    3. 按钮本身必须是浅灰、低饱和度区域；
    4. 按钮左侧必须主要是白色时间行；
    5. 按钮左侧如果是彩色视频或照片，直接排除。
    """
    height, width = image.shape[:2]

    scored_candidates: list[
        tuple[
            float,
            tuple[int, int, tuple[int, int, int, int]],
        ]
    ] = []

    for center_x, center_y, box in candidates:
        x1, y1, x2, y2 = box

        # =====================================================
        # 1. 真正的按钮一定非常靠近屏幕右侧
        # =====================================================
        if center_x < int(width * 0.90):
            continue

        if center_x > int(width * 0.99):
            continue

        # =====================================================
        # 2. 允许按钮出现在屏幕底部时间行
        # 原来的0.90会把截图里的真实按钮排除
        # =====================================================
        if center_y < int(height * 0.14):
            continue

        if center_y > int(height * 0.955):
            continue

        box_width = x2 - x1
        box_height = y2 - y1

        if box_width <= 0 or box_height <= 0:
            continue

        if not (
            int(width * 0.04)
            <= box_width
            <= int(width * 0.16)
        ):
            continue

        if not (
            int(height * 0.012)
            <= box_height
            <= int(height * 0.065)
        ):
            continue

        # =====================================================
        # 3. 检查按钮本体必须为浅灰低饱和区域
        # =====================================================
        button_patch = image[
            max(0, y1):min(height, y2),
            max(0, x1):min(width, x2),
        ]

        if button_patch.size == 0:
            continue

        button_hsv = cv2.cvtColor(
            button_patch,
            cv2.COLOR_BGR2HSV,
        )

        button_gray = cv2.cvtColor(
            button_patch,
            cv2.COLOR_BGR2GRAY,
        )

        low_saturation_ratio = float(
            np.mean(button_hsv[:, :, 1] <= 60)
        )

        light_ratio = float(
            np.mean(button_gray >= 195)
        )

        dark_ratio = float(
            np.mean(button_gray <= 190)
        )

        if low_saturation_ratio < 0.70:
            continue

        if light_ratio < 0.55:
            continue

        # 必须存在“··”产生的少量暗色像素
        if dark_ratio < 0.005:
            continue

        # =====================================================
        # 4. 检查按钮左侧的时间行背景
        #
        # 真按钮左边通常是：
        # “14分钟前”+ 大面积白色背景
        #
        # 视频误识别位置左边通常是：
        # 彩色画面、纸箱、人物或其他复杂内容
        # =====================================================
        row_y1 = max(
            0,
            center_y - int(height * 0.026),
        )

        row_y2 = min(
            height,
            center_y + int(height * 0.026),
        )

        left_x1 = max(
            0,
            x1 - int(width * 0.30),
        )

        left_x2 = max(
            left_x1 + 1,
            x1 - int(width * 0.012),
        )

        left_row = image[
            row_y1:row_y2,
            left_x1:left_x2,
        ]

        if left_row.size == 0:
            continue

        left_hsv = cv2.cvtColor(
            left_row,
            cv2.COLOR_BGR2HSV,
        )

        left_gray = cv2.cvtColor(
            left_row,
            cv2.COLOR_BGR2GRAY,
        )

        # 白色或浅灰背景比例
        white_ratio = float(
            np.mean(
                (left_hsv[:, :, 1] < 45)
                & (left_hsv[:, :, 2] > 215)
            )
        )

        # 明显彩色画面比例
        colorful_ratio = float(
            np.mean(
                left_hsv[:, :, 1] > 85
            )
        )

        # 深色大面积内容比例
        left_dark_ratio = float(
            np.mean(left_gray < 110)
        )

        # 左侧不是白色时间行，通常就是图片或视频区域
        if white_ratio < 0.58:
            continue

        if colorful_ratio > 0.20:
            continue

        if left_dark_ratio > 0.26:
            continue

        # =====================================================
        # 5. 计算可信度
        # =====================================================
        right_position_score = 1.0 - min(
            1.0,
            abs(
                center_x - width * 0.93
            ) / max(width * 0.10, 1),
        )

        score = (
            white_ratio * 100
            + low_saturation_ratio * 30
            + light_ratio * 25
            + right_position_score * 15
            - colorful_ratio * 60
        )

        scored_candidates.append(
            (
                score,
                (
                    center_x,
                    center_y,
                    box,
                ),
            )
        )

    # 先按可信度排序；
    # 可信度相同时，优先选择靠上的帖子
    scored_candidates.sort(
        key=lambda item: (
            -item[0],
            item[1][1],
        )
    )

    safe_candidates = [
        candidate
        for _, candidate in scored_candidates
    ]

    if safe_candidates:
        print("[安全候选详情]")

        for index, candidate in enumerate(
            safe_candidates,
            start=1,
        ):
            print(
                f"  候选{index}："
                f"坐标=({candidate[0]}, {candidate[1]})"
            )

    return safe_candidates

def scroll_until_ellipsis_visible(
    initial_image: Optional[np.ndarray] = None,
    max_swipes: int = 6,
    prefer_new_candidate: bool = False,
) -> tuple[
    np.ndarray,
    tuple[int, int, tuple[int, int, int, int]],
]:
    """
    寻找朋友圈右侧“··”按钮。

    prefer_new_candidate=False：
        第一次运行，选择屏幕中最靠上的正常候选。

    prefer_new_candidate=True：
        已经处理过至少一条，忽略上半屏残留的上一条帖子，
        优先选择屏幕下半部分的新帖子。
    """
    image = (
        initial_image
        if initial_image is not None
        else capture_screen()
    )

    for attempt in range(max_swipes + 1):
        raw_candidates = find_ellipsis_candidates(image)

        safe_candidates = filter_safe_ellipsis_candidates(
            image,
            raw_candidates,
        )

        print(
            f"[按钮扫描] 第 {attempt + 1} 次："
            f"原始候选 {len(raw_candidates)} 个，"
            f"安全候选 {len(safe_candidates)} 个"
        )

        if safe_candidates:
            height = image.shape[0]

            if prefer_new_candidate:
                # 已经处理过帖子后，只接受屏幕下半部分的新候选。
                # 上半部分通常是刚刚处理过、滑动后仍残留的旧帖子。
                new_candidates = [
                    candidate
                    for candidate in safe_candidates
                    if candidate[1] >= int(height * 0.55)
                ]

                if new_candidates:
                    # 下半屏中选择最靠上的新帖子，避免跳得太远
                    target = min(
                        new_candidates,
                        key=lambda item: item[1],
                    )

                    ignored_count = (
                        len(safe_candidates)
                        - len(new_candidates)
                    )

                    print(
                        f"[候选去旧] 已排除上半屏旧候选 "
                        f"{ignored_count} 个"
                    )

                    print(
                        f"找到下一条新帖“··”按钮："
                        f"({target[0]}, {target[1]})"
                    )

                    return image, target

                print(
                    "[候选去旧] 当前候选全部位于上半屏，"
                    "视为上一条帖子残留，继续上滑。"
                )

            else:
                # 第一次处理时，正常选择最靠上的候选
                target = min(
                    safe_candidates,
                    key=lambda item: item[1],
                )

                print(
                    f"找到真实“··”按钮："
                    f"({target[0]}, {target[1]})"
                )

                return image, target

        if attempt >= max_swipes:
            break

        print("当前没有可处理的新按钮，向上滑动后重新识别……")

        swipe_moments_up()
        image = capture_screen()

    raise RuntimeError(
        f"连续滑动 {max_swipes} 次后，"
        "仍未识别到下一条新的“··”按钮"
    )

def wait_until_screen_stable(
    timeout: float = 5.0,
    interval: float = 0.30,
    diff_threshold: float = 1.8,
    required_stable_frames: int = 3,
) -> np.ndarray:
    """
    连续多次截图，确认朋友圈已经停止滑动。

    返回最后一张稳定截图，不保存到硬盘。
    """
    start_time = time.time()

    previous_image = capture_screen()
    previous_gray = cv2.cvtColor(
        previous_image,
        cv2.COLOR_BGR2GRAY,
    )

    stable_count = 0
    latest_image = previous_image

    while time.time() - start_time < timeout:
        time.sleep(interval)

        current_image = capture_screen()
        current_gray = cv2.cvtColor(
            current_image,
            cv2.COLOR_BGR2GRAY,
        )

        height, width = current_gray.shape

        # 排除顶部状态栏和底部导航栏
        y1 = int(height * 0.12)
        y2 = int(height * 0.90)

        previous_roi = previous_gray[y1:y2, 0:width]
        current_roi = current_gray[y1:y2, 0:width]

        difference = cv2.absdiff(
            previous_roi,
            current_roi,
        )

        mean_difference = float(np.mean(difference))

        print(
            f"[稳定检测] 画面差异："
            f"{mean_difference:.2f}"
        )

        latest_image = current_image

        if mean_difference <= diff_threshold:
            stable_count += 1
        else:
            stable_count = 0

        if stable_count >= required_stable_frames:
            print("[稳定检测] 页面已经停止滑动")
            return current_image

        previous_gray = current_gray

    print("[稳定检测] 等待超时，使用最后一张截图")
    return latest_image

def swipe_moments_up(
    distance_ratio: float = 0.28,
) -> np.ndarray:
    """
    缓慢、小距离向上滑动，防止朋友圈产生较长惯性。
    滑动结束后等待页面完全静止。
    """
    image = capture_screen()
    height, width = image.shape[:2]

    start_x = width // 2
    start_y = int(height * 0.78)
    end_y = int(start_y - height * distance_ratio)

    end_y = max(
        int(height * 0.28),
        end_y,
    )

    print(
        f"缓慢滑动："
        f"({start_x}, {start_y}) "
        f"-> ({start_x}, {end_y})"
    )

    result = run_device_adb(
        [
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(start_x),
            str(end_y),
            "950",
        ]
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr
            or result.stdout
            or "朋友圈滑动失败"
        )

    return wait_until_screen_stable()

def tap_fresh_ellipsis(
    expected_x: int,
    expected_y: int,
) -> tuple[bool, Optional[tuple[int, int]]]:
    """
    点击前只短暂等待并重新截图校准。

    不再等待整张朋友圈完全静止，因为视频、头像等动态内容
    会导致画面差异始终大于稳定阈值。
    """
    time.sleep(0.45)

    fresh_image = capture_screen()

    raw_candidates = find_ellipsis_candidates(
        fresh_image
    )

    safe_candidates = filter_safe_ellipsis_candidates(
        fresh_image,
        raw_candidates,
    )

    print(
        f"[点击前校准] 原始候选 "
        f"{len(raw_candidates)} 个，"
        f"安全候选 {len(safe_candidates)} 个"
    )

    if not safe_candidates:
        print("[点击前校准] 没有找到新的安全候选")
        return False, None

    height, _ = fresh_image.shape[:2]

    target = min(
        safe_candidates,
        key=lambda item: (
            abs(item[1] - expected_y)
            + abs(item[0] - expected_x) * 0.35
        ),
    )

    fresh_x, fresh_y, box = target

    if abs(fresh_y - expected_y) > int(height * 0.12):
        print(
            "[点击前校准] 新旧按钮位置差距过大，"
            "取消本次点击。"
        )
        return False, None

    # 直接使用三点按钮识别中心
    # 不再根据可能包含额外灰色区域的外框重新计算
    click_x = fresh_x
    click_y = fresh_y

    print(
        f"[点击前校准] 旧坐标："
        f"({expected_x}, {expected_y})"
    )

    print(
        f"[点击前校准] 新坐标："
        f"({click_x}, {click_y})"
    )

    tap_xy(
        click_x,
        click_y,
        wait_after=0.65,
    )

    print(
        f"已点击最新“··”坐标："
        f"({click_x}, {click_y})"
    )

    return True, (click_x, click_y)

def is_fullscreen_image_viewer(
    image: np.ndarray,
) -> bool:
    """
    判断是否误点进入朋友圈图片全屏查看器。

    全屏图片页面通常：
    - 大部分屏幕被照片占据；
    - 白色朋友圈列表背景很少；
    - 底部有较深的导航/遮罩区域。
    """
    height, width = image.shape[:2]

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]

    content = image[
        int(height * 0.06):int(height * 0.90),
        0:width,
    ]

    content_hsv = cv2.cvtColor(
        content,
        cv2.COLOR_BGR2HSV,
    )

    white_ratio = float(
        np.mean(
            (content_hsv[:, :, 1] < 35)
            & (content_hsv[:, :, 2] > 238)
        )
    )

    bottom_value = value[
        int(height * 0.88):int(height * 0.99),
        :
    ]

    bottom_dark_ratio = float(
        np.mean(bottom_value < 130)
    )

    return (
        white_ratio < 0.20
        and bottom_dark_ratio > 0.12
    )

SEEN_POSTS_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "liked_post_hashes.txt"
)


def load_seen_post_hashes() -> set[str]:
    """
    读取历史已处理帖子指纹。
    程序重启后也不会重新处理同一条内容。
    """
    if not SEEN_POSTS_FILE.exists():
        return set()

    return {
        line.strip()
        for line in SEEN_POSTS_FILE.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    }


def save_seen_post_hash(post_hash: str) -> None:
    SEEN_POSTS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with SEEN_POSTS_FILE.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(post_hash + "\n")


def move_to_next_post() -> np.ndarray:
    """
    只有确认当前仍在朋友圈后才允许滑动。
    """
    image = ensure_moments_page()
    height, width = image.shape[:2]

    start_x = width // 2
    start_y = int(height * 0.78)
    end_y = int(height * 0.42)

    print(
        f"移动到下一条朋友圈："
        f"({start_x}, {start_y}) -> "
        f"({start_x}, {end_y})"
    )

    result = run_device_adb(
        [
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(start_x),
            str(end_y),
            "900",
        ]
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr
            or result.stdout
            or "朋友圈滑动失败"
        )

    stable_image = wait_until_screen_stable(
        timeout=6.0,
        interval=0.30,
        diff_threshold=1.8,
        required_stable_frames=3,
    )

    page_result = classify_page(stable_image)

    if page_result.page not in {
        "moments",
        "possible_moments",
    }:
        print(
            "滑动后发现页面不是朋友圈，"
            "立即停止并重新进入朋友圈。"
        )
        return ensure_moments_page()

    return stable_image

def get_like_menu_state() -> str:
    """
    读取当前微信弹出菜单状态。

    返回：
    - "unliked"：显示“赞”，可以点赞
    - "liked"：显示“取消/取消赞”，已经点赞，禁止再点
    - "unknown"：无法确认，为防止取消点赞，跳过
    """
    remote_path = "/sdcard/wechat_like_menu.xml"

    dump_result = run_device_adb(
        [
            "shell",
            "uiautomator",
            "dump",
            remote_path,
        ],
        timeout=15,
    )

    if dump_result.returncode != 0:
        return "unknown"

    read_result = run_device_adb(
        [
            "exec-out",
            "cat",
            remote_path,
        ],
        timeout=15,
    )

    if read_result.returncode != 0:
        return "unknown"

    xml_text = read_result.stdout or ""

    # 先判断取消，必须放在“赞”前面
    cancel_keywords = [
        'text="取消"',
        'text="取消赞"',
        'content-desc="取消"',
        'content-desc="取消赞"',
    ]

    if any(keyword in xml_text for keyword in cancel_keywords):
        return "liked"

    like_keywords = [
        'text="赞"',
        'content-desc="赞"',
    ]

    if any(keyword in xml_text for keyword in like_keywords):
        return "unliked"

    return "unknown"

def ensure_moments_page() -> np.ndarray:
    """
    每次准备滑动前确认仍在朋友圈。

    如果程序已经误退到发现页或微信主页，
    自动重新进入朋友圈，禁止在错误页面继续滑动。
    """
    image = capture_screen()
    result = classify_page(image)

    print(
        f"[操作前页面检查] {result.page} "
        f"| {result.reason}"
    )

    if result.page in {
        "moments",
        "possible_moments",
    }:
        return image

    print(
        "当前已经不在朋友圈，"
        "停止本次滑动并重新进入朋友圈。"
    )

    image, _ = run_navigation()
    return image


def close_like_popup_safely(
    more_x: int,
    more_y: int,
) -> np.ndarray:
    """
    只有截图明确识别到点赞菜单时才按返回键。

    如果菜单已经消失，则绝不按返回键，
    防止直接退出朋友圈。
    """
    current_image = capture_screen()

    popup = find_dark_popup(
        current_image,
        expected_x=more_x,
        expected_y=more_y,
    )

    if popup is not None:
        print("确认点赞菜单仍然存在，按一次返回键关闭菜单。")
        press_back()
    else:
        print(
            "当前没有检测到点赞菜单，"
            "不执行返回键，防止退出朋友圈。"
        )

    return ensure_moments_page()

def get_like_point_from_ellipsis(
    image: np.ndarray,
    more_x: int,
    more_y: int,
) -> tuple[int, int]:
    """
    根据“··”按钮位置计算“赞”的安全点击位置。

    点赞菜单与“··”按钮基本处于同一水平线，
    不再使用可能包含视频区域的菜单外框纵坐标。
    """
    height, width = image.shape[:2]

    # “赞”位于“··”左侧约半个屏幕宽度
    like_x = int(
        more_x - width * 0.50
    )

    # 关键：纵坐标直接使用“··”按钮中心
    like_y = int(more_y)

    # 安全限制，避免坐标超出正常菜单范围
    like_x = max(
        int(width * 0.30),
        min(int(width * 0.56), like_x),
    )

    like_y = max(
        int(height * 0.12),
        min(int(height * 0.94), like_y),
    )

    return like_x, like_y

def confirm_moments_page() -> np.ndarray:
    """
    每次操作前确认当前仍在朋友圈。
    如果程序已经误退到其他页面，自动重新进入朋友圈。
    """
    image = capture_screen()
    result = classify_page(image)
    
    print(f"[操作前页面检查] 当前页面: {result.page} | {result.reason}")

    if result.page in {"moments", "possible_moments"}:
        return image

    print("检测到当前不在朋友圈，尝试重新进入...")
    restored_image, _ = run_navigation()
    return restored_image

def close_popup_without_back(more_x: int, more_y: int) -> np.ndarray:
    """
    菜单存在时点一下关闭，确保不误触退出。
    """
    current_image = capture_screen()
    popup = find_dark_popup(current_image, expected_x=more_x, expected_y=more_y)
    if popup:
        tap_xy(more_x, more_y, wait_after=0.7)
    return confirm_moments_page()

def safely_move_next() -> np.ndarray:
    """
    确认在朋友圈后，才允许向上滑动。
    """
    confirm_moments_page()
    return move_to_next_post()

def classify_like_state_by_screenshot(
    image: np.ndarray,
    popup: tuple[int, int, int, int],
    more_y: int,
) -> str:
    """
    根据截图判断菜单左侧显示的是“赞”还是“取消”。

    微信当前界面特征：
    - “赞”：白色空心爱心；
    - “取消”：红色实心爱心。

    返回：
    - liked：菜单显示“取消”，说明已经点赞；
    - unliked：菜单显示“赞”，说明尚未点赞；
    - unknown：无法可靠判断。
    """
    height, width = image.shape[:2]
    x1, y1, x2, y2 = popup

    popup_width = x2 - x1

    # 只分析菜单左半部分
    left_x1 = max(0, x1)
    left_x2 = min(
        width,
        x1 + int(popup_width * 0.48),
    )

    # 只截取“··”所在水平线附近，
    # 避免错误菜单框包含上方照片或视频
    band_half_height = int(height * 0.035)

    left_y1 = max(
        y1,
        more_y - band_half_height,
    )

    left_y2 = min(
        y2,
        more_y + band_half_height,
    )

    roi = image[
        left_y1:left_y2,
        left_x1:left_x2,
    ]

    if roi.size == 0:
        print("[点赞状态识别] 菜单左侧区域为空")
        return "unknown"

    hsv = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2HSV,
    )

    gray = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2GRAY,
    )

    # 红色分为HSV两端
    red_mask_1 = cv2.inRange(
        hsv,
        np.array([0, 90, 75], dtype=np.uint8),
        np.array([12, 255, 255], dtype=np.uint8),
    )

    red_mask_2 = cv2.inRange(
        hsv,
        np.array([168, 90, 75], dtype=np.uint8),
        np.array([179, 255, 255], dtype=np.uint8),
    )

    red_mask = cv2.bitwise_or(
        red_mask_1,
        red_mask_2,
    )

    red_mask = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_OPEN,
        np.ones((3, 3), np.uint8),
    )

    red_ratio = float(
        np.mean(red_mask > 0)
    )

    # 找最大红色连通区域
    count, _, stats, _ = (
        cv2.connectedComponentsWithStats(
            red_mask,
            connectivity=8,
        )
    )

    largest_red_area = 0

    for index in range(1, count):
        area = int(
            stats[index, cv2.CC_STAT_AREA]
        )

        largest_red_area = max(
            largest_red_area,
            area,
        )

    roi_area = roi.shape[0] * roi.shape[1]

    dark_ratio = float(
        np.mean(gray < 155)
    )

    bright_ratio = float(
        np.mean(gray > 205)
    )

    print(
        "[点赞状态识别] "
        f"红色比例={red_ratio:.4f}，"
        f"最大红色区域={largest_red_area}，"
        f"深色比例={dark_ratio:.3f}，"
        f"亮色比例={bright_ratio:.3f}"
    )

    # 红色实心爱心，菜单显示“取消”
    if (
        largest_red_area
        >= max(18, int(roi_area * 0.002))
        and red_ratio >= 0.0015
    ):
        print(
            "[点赞状态识别] 检测到红色爱心："
            "当前显示“取消”"
        )

        return "liked"

    # 菜单本身为深色，同时存在白色爱心和白色文字
    if (
        dark_ratio >= 0.45
        and bright_ratio >= 0.018
    ):
        print(
            "[点赞状态识别] 未检测到红色爱心，"
            "检测到白色图标：当前显示“赞”"
        )

        return "unliked"

    print("[点赞状态识别] 无法可靠确定状态")
    return "unknown"

def exit_moments_after_liked(
    more_x: int,
    more_y: int,
) -> None:
    """
    检测到“取消”后：

    1. 关闭当前弹出的赞/评论菜单；
    2. 再返回一次退出朋友圈；
    3. 不继续处理后面的内容。
    """
    current_image = capture_screen()

    popup = find_dark_popup(
        current_image,
        expected_x=more_x,
        expected_y=more_y,
    )

    if popup is not None:
        print("先点击原来的“··”，关闭菜单。")

        tap_xy(
            more_x,
            more_y,
            wait_after=0.8,
        )

    current_image = capture_screen()
    result = classify_page(current_image)

    if result.page in {
        "moments",
        "possible_moments",
    }:
        print("检测到已点赞内容，正在退出朋友圈。")
        press_back()

    else:
        print(
            f"当前页面已经不是朋友圈："
            f"{result.page}"
        )

def main() -> None:
    print("=" * 68)
    print("微信朋友圈：截图判断点赞状态")
    print("规则：最多检查15条，发现“取消”立即退出朋友圈")
    print("=" * 68)

    try:
        moments_image, _ = run_navigation()

        checked_count = 0
        liked_count = 0
        target_check_count = 15

        while checked_count < target_check_count:
            print(
                f"\n--- 正在检查第 "
                f"{checked_count + 1}/{target_check_count} 条 ---"
            )

            # =====================================================
            # 1. 确认当前仍然在朋友圈
            # =====================================================
            moments_image = confirm_moments_page()

            # =====================================================
            # 2. 寻找当前帖子真实“··”按钮
            # =====================================================
            try:
                moments_image, target = (
                    scroll_until_ellipsis_visible(
                        initial_image=moments_image,
                        max_swipes=6,

                        # 第一条正常选择；
                        # 从第二条开始排除上半屏残留的旧帖子
                        prefer_new_candidate=(checked_count > 0),
                    )
                )
            except Exception as exc:
                print(f"寻找“··”失败：{exc}")

                moments_image = safely_move_next()
                continue

            more_x = target[0]
            more_y = target[1]

            print(
                f"当前“··”按钮坐标："
                f"({more_x}, {more_y})"
            )

            # =====================================================
            # 3. 点击前重新校准
            # =====================================================
            clicked, fresh_point = tap_fresh_ellipsis(
                expected_x=more_x,
                expected_y=more_y,
            )

            if not clicked or fresh_point is None:
                print("点击前校准失败，移动到下一条。")

                checked_count += 1
                moments_image = safely_move_next()
                continue

            more_x, more_y = fresh_point

            # =====================================================
            # 4. 等待“赞/评论”菜单出现
            # =====================================================
            menu_image, popup = wait_for_like_popup(
                expected_x=more_x,
                expected_y=more_y,
                attempts=5,
            )

            if popup is None:
                print(
                    "没有识别到点赞菜单，"
                    "本条不点击，移动到下一条。"
                )

                checked_count += 1
                moments_image = safely_move_next()
                continue

            print(f"已识别点赞菜单：{popup}")

            # =====================================================
            # 5. 直接通过截图颜色判断赞或取消
            # =====================================================
            menu_state = classify_like_state_by_screenshot(
                image=menu_image,
                popup=popup,
                more_y=more_y,
            )

            print(f"截图判断菜单状态：{menu_state}")

            # =====================================================
            # 6. 检测到“取消”：
            #    说明当前内容以前已经点赞，立即退出朋友圈
            # =====================================================
            if menu_state == "liked":
                print(
                    "\n检测到菜单显示“取消”，"
                    "说明连续检查范围内出现已点赞内容。"
                )

                exit_moments_after_liked(
                    more_x=more_x,
                    more_y=more_y,
                )

                print(
                    f"任务结束：共检查 {checked_count + 1} 条，"
                    f"本轮确认点赞 {liked_count} 条。"
                )

                return

            # =====================================================
            # 7. 检测到“赞”
            # =====================================================
            if menu_state == "unliked":
                print("当前菜单显示“赞”，自动执行点赞。")

                # 直接计算点赞坐标，无需人工确认
                like_point = get_like_point_from_ellipsis(
                    image=menu_image,
                    more_x=more_x,
                    more_y=more_y,
                )

                print(f"准备点击“赞”：{like_point}")

                # 执行点赞
                tap_xy(
                    like_point[0],
                    like_point[1],
                    wait_after=1.2,
                )

                liked_count += 1
                print(f"本轮已经自动点赞 {liked_count} 条。")
                
                # 记录数据库操作在后面执行，代码中已包含
                # 这里移除 else 的人工确认分支，直接继续进行后续的 save_dedup_record 和滑动

            else:
                # 状态不为 unliked (如 unknown 或已点赞)，自动跳过
                print(f"当前状态为 {menu_state}，无需点赞。")
                
                # 即使不点赞，也尝试关闭菜单
                current_image = capture_screen()
                current_popup = find_dark_popup(
                    current_image, expected_x=more_x, expected_y=more_y,
                )
                if current_popup is not None:
                    tap_xy(more_x, more_y, wait_after=0.7)

            # 更新计数和移动到下一条
            checked_count += 1
            moments_image = safely_move_next()
            continue

            # =====================================================
            # 8. 无法判断时不点击
            # =====================================================
            print(
                "截图仍无法判断“赞/取消”，"
                "当前内容安全跳过。"
            )

            current_image = capture_screen()

            current_popup = find_dark_popup(
                current_image,
                expected_x=more_x,
                expected_y=more_y,
            )

            if current_popup is not None:
                tap_xy(
                    more_x,
                    more_y,
                    wait_after=0.7,
                )

            checked_count += 1
            moments_image = safely_move_next()

        print(
            f"\n已经连续检查 {checked_count} 条，"
            "没有检测到“取消”。"
        )

        print(
            f"本轮人工确认点赞："
            f"{liked_count} 条。"
        )

    except KeyboardInterrupt:
        print("\n用户主动停止程序。")

    except Exception as exc:
        print(f"\n任务运行出错：{exc}")

    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()