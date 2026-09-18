# -*- coding: utf-8 -*-
"""
微信朋友圈点赞：2个微信同时运行（右侧竖屏）

功能：
1. 自动检测右侧竖屏
2. 2个微信上下排列在右侧竖屏
3. 启动2个worker进程，每个绑定一个微信
4. 自动重启异常退出的worker

使用方法：
1. 先打开2个微信窗口
2. 运行此脚本：python launch_4_wechat.py
3. 停止所有：python launch_4_wechat.py --stop
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import win32api
from pywinauto import Desktop


# ============================================================
# 路径配置
# ============================================================

PYTHON_EXE = sys.executable

WORKDIR = Path(__file__).resolve().parent

WORKER_SCRIPT = WORKDIR / "wechat_like_worker_by_pid.py"

LOG_DIR = WORKDIR / "logs" / "wechat_4x"

RUNTIME_DIR = WORKDIR / "_wechat_4x_runtime"


# ============================================================
# 窗口尺寸
# ============================================================

# 微信主窗口放大约 20%，保证左侧图一和右侧扩展入口都在可识别区域内。
MAIN_W = 1080
MAIN_H = 780

MOMENTS_W = 576
MOMENTS_H = 558

GAP = 20
MARGIN = 10


# ============================================================
# 运行参数
# ============================================================

WECHAT_COUNT = 2

START_INTERVAL = 6
CHECK_INTERVAL = 60
RESTART_DELAY = 10
MAX_RESTARTS_PER_HOUR = 5

ENABLE_TIME_WINDOW = True
RUN_START_HOUR = 8
RUN_END_HOUR = 0


# ============================================================
# 工具函数
# ============================================================

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_print(msg):
    print(f"[{now_text()}] {msg}", flush=True)


def is_in_run_time():
    if not ENABLE_TIME_WINDOW:
        return True
    hour = datetime.now().hour
    if RUN_START_HOUR == RUN_END_HOUR:
        return True
    if RUN_START_HOUR < RUN_END_HOUR:
        return RUN_START_HOUR <= hour < RUN_END_HOUR
    return hour >= RUN_START_HOUR or hour < RUN_END_HOUR


# ============================================================
# 屏幕和位置
# ============================================================

def get_rightmost_screen():
    """找到最右边的屏幕（x1最大的那个）。"""
    try:
        monitors = win32api.EnumDisplayMonitors()
    except Exception as exc:
        log_print(f"[屏幕] 枚举失败：{exc}")
        return None

    best = None

    for idx, monitor in enumerate(monitors):
        rect = monitor[2]
        x1, y1, x2, y2 = rect
        w = x2 - x1
        h = y2 - y1

        log_print(
            f"[屏幕{idx}] 位置=({x1},{y1})-({x2},{y2}) "
            f"大小={w}x{h}"
        )

        if w < 100 or h < 100:
            continue

        # 最右边 = x1 最大
        if best is None or x1 > best["x1"]:
            best = {
                "index": idx,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "width": w,
                "height": h,
            }

    return best


def calc_2_positions_on_screen(screen):
    """
    在一个屏幕上计算2个账号的位置，上下排列。
    """
    sx1, sy1 = screen["x1"], screen["y1"]
    sw, sh = screen["width"], screen["height"]

    pair_w = MAIN_W + GAP + MOMENTS_W
    pair_h = max(MAIN_H, MOMENTS_H)

    # 居中水平位置
    x = sx1 + (sw - pair_w) // 2
    x = max(x, sx1 + MARGIN)

    # 上下排列
    y1 = sy1 + MARGIN
    y2 = sy1 + MARGIN + pair_h + GAP

    return [
        {
            "main": (x, y1, MAIN_W, MAIN_H),
            "moments": (x + MAIN_W + GAP, y1, MOMENTS_W, MOMENTS_H),
            "screen": f"竖屏{screen['index']}",
        },
        {
            "main": (x, y2, MAIN_W, MAIN_H),
            "moments": (x + MAIN_W + GAP, y2, MOMENTS_W, MOMENTS_H),
            "screen": f"竖屏{screen['index']}",
        },
    ]


# ============================================================
# 微信窗口检测
# ============================================================

def find_all_wechat_windows():
    desktop = Desktop(backend="uia")
    results = []

    for window in desktop.windows():
        try:
            title = window.window_text()
            pid = window.element_info.process_id
            process_name = psutil.Process(pid).name().lower()

            if process_name not in ["weixin.exe", "wechat.exe"]:
                continue

            if not window.is_visible():
                continue

            rect = window.rectangle()
            width = rect.width()
            height = rect.height()

            if width < 100 or height < 100:
                continue

            hwnd = int(window.handle)

            score = 0
            if title == "微信":
                score += 10
            if width >= 700 and height >= 500:
                score += 5
            if width >= 400 and height >= 400:
                score += 2

            results.append({
                "window": window,
                "hwnd": hwnd,
                "pid": pid,
                "title": title,
                "left": rect.left,
                "top": rect.top,
                "width": width,
                "height": height,
                "score": score,
            })

        except Exception:
            continue

    # 多开微信可能共用同一个进程 PID，必须按顶层窗口 HWND 去重，
    # 否则 A/B 会被错误合并成一个 Worker。
    hwnd_map = {}
    for item in results:
        hwnd = item["hwnd"]
        if hwnd not in hwnd_map or item["score"] > hwnd_map[hwnd]["score"]:
            hwnd_map[hwnd] = item

    main_windows = sorted(
        hwnd_map.values(),
        key=lambda x: (x["left"], x["top"]),
    )

    return main_windows


# ============================================================
# 进程管理
# ============================================================

def stop_all_processes():
    current_pid = os.getpid()
    killed = 0

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = proc.info["pid"]
            if pid == current_pid:
                continue

            name = (proc.info.get("name") or "").lower()
            cmdline_list = proc.info.get("cmdline") or []
            cmdline = " ".join(cmdline_list)

            if "python" not in name:
                continue

            is_worker = "wechat_like_worker_by_pid.py" in cmdline
            is_manager = "launch_4_wechat.py" in cmdline and pid != current_pid

            if not is_worker and not is_manager:
                continue

            log_print(f"[查杀] PID={pid} CMD={cmdline}")

            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

            killed += 1

        except Exception:
            continue

    log_print(f"[完成] 已停止进程数量：{killed}")


def start_worker(account, pid, hwnd, pos):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    log_path = LOG_DIR / f"worker_{account}_{today}.log"

    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    log_file.write("\n" + "=" * 60 + "\n")
    log_file.write(
        f"[{now_text()}] 启动 worker {account}，PID={pid}，HWND={hwnd}\n"
    )
    log_file.write("=" * 60 + "\n")

    main_x, main_y, main_w, main_h = pos["main"]
    mom_x, mom_y, mom_w, mom_h = pos["moments"]

    cmd = [
        PYTHON_EXE,
        "-X", "utf8",
        "-u",
        str(WORKER_SCRIPT),
        "--pid", str(pid),
        "--hwnd", str(hwnd),
        "--main", str(main_x), str(main_y), str(main_w), str(main_h),
        "--moments", str(mom_x), str(mom_y), str(mom_w), str(mom_h),
    ]

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        cmd,
        cwd=str(WORKDIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )

    log_print(
        f"[启动] 账号 {account} | PID={pid} | HWND={hwnd} | "
        f"屏幕={pos['screen']} | "
        f"主窗口=({main_x},{main_y},{main_w},{main_h}) | "
        f"朋友圈=({mom_x},{mom_y},{mom_w},{mom_h})"
    )

    return {
        "account": account,
        "pid": pid,
        "hwnd": hwnd,
        "process": process,
        "log_file": log_file,
        "log_path": log_path,
        "restart_times": [],
    }


# ============================================================
# 主逻辑
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="2个微信同时运行朋友圈点赞（右侧竖屏）"
    )
    parser.add_argument("--stop", action="store_true", help="停止所有worker")
    parser.add_argument("--list", action="store_true", help="只列出位置，不启动")
    parser.add_argument("--screens", action="store_true", help="只显示屏幕信息")
    args = parser.parse_args()

    if args.stop:
        stop_all_processes()
        return

    # 检测屏幕
    log_print("=" * 60)
    log_print("检测屏幕...")
    log_print("=" * 60)

    right_screen = get_rightmost_screen()

    if right_screen is None:
        log_print("[错误] 没有检测到屏幕。")
        return

    log_print(
        f"[选中] 最右侧屏幕：位置=({right_screen['x1']},{right_screen['y1']})-"
        f"({right_screen['x2']},{right_screen['y2']}) "
        f"大小={right_screen['width']}x{right_screen['height']}"
    )

    if args.screens:
        return

    # 计算位置
    positions = calc_2_positions_on_screen(right_screen)

    accounts = ["A", "B"]

    log_print("=" * 60)
    log_print("窗口位置分配：")
    for i, pos in enumerate(positions):
        account = accounts[i]
        main_x, main_y, main_w, main_h = pos["main"]
        mom_x, mom_y, mom_w, mom_h = pos["moments"]
        log_print(
            f"  账号 {account} | "
            f"主窗口=({main_x},{main_y},{main_w},{main_h}) | "
            f"朋友圈=({mom_x},{mom_y},{mom_w},{mom_h})"
        )
    log_print("=" * 60)

    # 检测微信
    log_print("检测微信窗口...")
    wechat_list = find_all_wechat_windows()

    if not wechat_list:
        log_print("[错误] 没有检测到微信窗口。")
        return

    log_print(f"检测到 {len(wechat_list)} 个微信窗口：")
    for i, w in enumerate(wechat_list):
        log_print(
            f"  [{i+1}] PID={w['pid']} | "
            f"位置=({w['left']},{w['top']},{w['width']},{w['height']})"
        )

    wechat_count = min(len(wechat_list), WECHAT_COUNT)

    if wechat_count < WECHAT_COUNT:
        log_print(f"[警告] 只检测到 {wechat_count} 个微信窗口。")

    if args.list:
        log_print("仅列出位置，不启动。")
        return

    # 启动
    tasks = {}

    try:
        for i in range(wechat_count):
            account = accounts[i]
            pos = positions[i]

            tasks[account] = start_worker(
                account, wechat_list[i]["pid"], wechat_list[i]["hwnd"], pos
            )
            time.sleep(START_INTERVAL)

        log_print("=" * 60)
        log_print(f"已启动 {len(tasks)} 个worker，进入监控模式。")
        log_print("按 Ctrl+C 停止所有。")
        log_print("=" * 60)

        while True:
            if not is_in_run_time():
                if tasks:
                    log_print("[时间控制] 不在运行时间，停止所有worker。")
                    for account, item in list(tasks.items()):
                        try:
                            item["process"].terminate()
                            item["process"].wait(timeout=5)
                        except Exception:
                            pass
                        try:
                            item["log_file"].close()
                        except Exception:
                            pass
                    tasks.clear()

                log_print("[时间控制] 等待下一次检查...")
                time.sleep(CHECK_INTERVAL)
                continue

            for account, item in list(tasks.items()):
                process = item["process"]

                if process.poll() is None:
                    continue

                exit_code = process.returncode
                log_print(
                    f"[退出] 账号 {account} 已退出，ExitCode={exit_code}"
                )

                try:
                    item["log_file"].close()
                except Exception:
                    pass

                now = datetime.now()
                one_hour_ago = now - timedelta(hours=1)
                restart_times = [
                    t for t in item.get("restart_times", [])
                    if t > one_hour_ago
                ]
                item["restart_times"] = restart_times

                if len(restart_times) >= MAX_RESTARTS_PER_HOUR:
                    log_print(
                        f"[暂停] 账号 {account} 1小时内重启过多，不再重启。"
                    )
                    del tasks[account]
                    continue

                log_print(
                    f"[重启] 账号 {account} 将在 {RESTART_DELAY} 秒后重启。"
                )
                time.sleep(RESTART_DELAY)

                wechat_list = find_all_wechat_windows()
                target_pid = None
                target_hwnd = None
                for j, w in enumerate(wechat_list):
                    if accounts[j] == account:
                        target_pid = w["pid"]
                        target_hwnd = w["hwnd"]
                        break

                if target_pid is None or target_hwnd is None:
                    log_print(f"[跳过] 找不到账号 {account} 的微信窗口。")
                    continue

                idx = accounts.index(account)
                pos = positions[idx]
                new_item = start_worker(account, target_pid, target_hwnd, pos)
                new_item["restart_times"] = item.get("restart_times", [])
                tasks[account] = new_item

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        log_print("[手动停止] 正在停止所有worker...")
        for account, item in list(tasks.items()):
            try:
                item["process"].terminate()
                item["process"].wait(timeout=5)
            except Exception:
                try:
                    item["process"].kill()
                except Exception:
                    pass
            try:
                item["log_file"].close()
            except Exception:
                pass
        tasks.clear()
        log_print("[结束] 已全部停止。")

    except Exception as exc:
        log_print(f"[异常] {exc}")
        for account, item in list(tasks.items()):
            try:
                item["process"].terminate()
            except Exception:
                pass
            try:
                item["log_file"].close()
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
