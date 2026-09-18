# -*- coding: utf-8 -*-
"""Auto-detect WeChat window PIDs and start the PID-bound like workers."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
from pywinauto import Desktop


PYTHON_EXE = Path(r"C:\Users\Deple\anaconda3\python.exe")
WORKDIR = Path(r"E:\wechat_like_helper\src")
WORKER = WORKDIR / "wechat_like_worker_by_pid.py"

SLOTS = [
    {
        "name": "TOP",
        "main": (-1900, 20, 900, 520),
        "moments": (-970, 20, 560, 520),
    },
    {
        "name": "BOTTOM",
        "main": (-1900, 560, 900, 500),
        "moments": (-970, 560, 560, 500),
    },
]

WECHAT_PROCESS_NAMES = {"weixin.exe", "wechat.exe"}


def log(message: str) -> None:
    print(message, flush=True)


def is_wechat_pid(pid: int) -> bool:
    try:
        return psutil.Process(pid).name().lower() in WECHAT_PROCESS_NAMES
    except Exception:
        return False


def score_window(title: str, left: int, top: int, width: int, height: int) -> int:
    score = 0

    if title in {"微信", "WeChat"}:
        score += 20

    if width >= 700 and height >= 500:
        score += 10

    if left <= -30000 and top <= -30000 and title in {"微信", "WeChat"}:
        score += 5

    if width < 300 or height < 300:
        score -= 20

    return score


def detect_wechat_windows() -> list[dict]:
    desktop = Desktop(backend="uia")
    by_pid: dict[int, dict] = {}

    for window in desktop.windows():
        try:
            pid = int(window.element_info.process_id)

            if not is_wechat_pid(pid):
                continue

            rect = window.rectangle()
            title = window.window_text()
            width = rect.width()
            height = rect.height()
            visible = bool(window.is_visible())
            score = score_window(title, rect.left, rect.top, width, height)

            log(
                "[scan] "
                f"PID={pid} title={title!r} visible={visible} "
                f"rect=({rect.left},{rect.top},{width},{height}) score={score}"
            )

            if not visible and rect.left > -30000:
                continue

            if score <= 0:
                continue

            candidate = {
                "pid": pid,
                "title": title,
                "left": int(rect.left),
                "top": int(rect.top),
                "width": int(width),
                "height": int(height),
                "score": int(score),
            }

            old = by_pid.get(pid)
            if old is None or candidate["score"] > old["score"]:
                by_pid[pid] = candidate

        except Exception:
            continue

    candidates = list(by_pid.values())
    candidates.sort(key=lambda item: (-item["score"], item["left"], item["top"]))
    selected = candidates[: len(SLOTS)]
    selected.sort(key=lambda item: (item["top"], item["left"], item["pid"]))
    return selected


def stop_old_pid_workers() -> None:
    current_pid = os.getpid()
    killed = 0

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = int(proc.info["pid"])
            if pid == current_pid:
                continue

            name = (proc.info.get("name") or "").lower()
            cmdline = " ".join(proc.info.get("cmdline") or [])

            if "python" not in name:
                continue

            if "wechat_like_worker_by_pid.py" not in cmdline:
                continue

            log(f"[stop] old worker PID={pid}")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            killed += 1

        except Exception:
            continue

    log(f"[stop] old workers stopped: {killed}")


def start_worker(pid: int, slot: dict) -> subprocess.Popen:
    main = [str(value) for value in slot["main"]]
    moments = [str(value) for value in slot["moments"]]

    cmd = [
        str(PYTHON_EXE),
        "-X",
        "utf8",
        "-u",
        str(WORKER),
        "--pid",
        str(pid),
        "--main",
        *main,
        "--moments",
        *moments,
    ]

    log(
        f"[start] slot={slot['name']} PID={pid} "
        f"main={slot['main']} moments={slot['moments']}"
    )

    return subprocess.Popen(
        cmd,
        cwd=str(WORKDIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def main() -> int:
    if not PYTHON_EXE.exists():
        log(f"[ERROR] Python not found: {PYTHON_EXE}")
        return 1

    if not WORKER.exists():
        log(f"[ERROR] worker not found: {WORKER}")
        return 1

    log("=" * 72)
    log("Auto-detect current WeChat main window PIDs")
    log("=" * 72)

    windows = detect_wechat_windows()

    if len(windows) < len(SLOTS):
        log("")
        log(f"[ERROR] Need {len(SLOTS)} WeChat main windows, found {len(windows)}.")
        log("Please open both WeChat windows first, then run this bat again.")
        return 2

    log("")
    for index, window in enumerate(windows, start=1):
        log(
            f"[selected {index}] PID={window['pid']} title={window['title']!r} "
            f"rect=({window['left']},{window['top']},"
            f"{window['width']},{window['height']})"
        )

    log("")
    stop_old_pid_workers()

    for window, slot in zip(windows, SLOTS):
        start_worker(int(window["pid"]), slot)
        time.sleep(8)

    log("")
    log("[OK] Started two PID-bound WeChat like workers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
