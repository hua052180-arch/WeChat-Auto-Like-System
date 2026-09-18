"""测试 Windows PrintWindow 图形捕获，不移动鼠标、不改变窗口前台状态。"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np
import psutil
import win32gui


PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020


def capture_window_graphics(hwnd: int) -> np.ndarray:
    """使用 PrintWindow 从窗口自身绘制内容，不依赖屏幕是否被遮挡。"""
    hwnd = int(hwnd)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = int(right - left), int(bottom - top)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"窗口尺寸无效：{width}x{height}")

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        raise ctypes.WinError()
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
    try:
        # 先清空为黑色，避免窗口拒绝绘制时误把旧数据当有效画面。
        gdi32.PatBlt(mem_dc, 0, 0, width, height, 0x00000042)
        ok = bool(user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT))
        if not ok:
            ok = bool(user32.PrintWindow(hwnd, mem_dc, 0))
        if not ok:
            raise RuntimeError(f"PrintWindow 返回失败，HWND={hwnd}")

        class BitmapInfoHeader(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        header = BitmapInfoHeader()
        header.biSize = ctypes.sizeof(BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0
        buffer = (ctypes.c_ubyte * (width * height * 4))()
        copied = gdi32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            ctypes.byref(buffer),
            ctypes.byref(header),
            0,
        )
        if copied != height:
            raise RuntimeError(f"GetDIBits 失败：{copied}/{height}")
        return cv2.cvtColor(
            np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4),
            cv2.COLOR_BGRA2BGR,
        )
    finally:
        gdi32.SelectObject(mem_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def list_wechat_windows() -> list[tuple[int, str, int]]:
    rows: list[tuple[int, str, int]] = []

    def callback(hwnd: int, _extra: int) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        try:
            _, pid = win32gui.GetWindowThreadProcessId(hwnd)
            name = psutil.Process(pid).name().lower()
        except Exception:
            return
        if name not in {"wechat.exe", "weixin.exe"}:
            return
        rows.append((int(hwnd), title, int(pid)))

    win32gui.EnumWindows(callback, 0)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, help="指定窗口句柄")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).with_name("graphics_capture_test")),
        help="输出目录",
    )
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)

    targets = []
    if args.hwnd:
        targets = [(args.hwnd, win32gui.GetWindowText(args.hwnd), 0)]
    else:
        targets = list_wechat_windows()
    if not targets:
        raise SystemExit("没有找到可捕获的微信窗口")

    for hwnd, title, pid in targets:
        try:
            image = capture_window_graphics(hwnd)
            path = output / f"printwindow_{hwnd}.png"
            cv2.imwrite(str(path), image)
            print(f"PASS HWND={hwnd} PID={pid} TITLE={title!r} SIZE={image.shape[1]}x{image.shape[0]} FILE={path}")
        except Exception as exc:
            print(f"FAIL HWND={hwnd} TITLE={title!r}: {exc}")


if __name__ == "__main__":
    main()
