# -*- coding: utf-8 -*-
"""
微信朋友圈点赞：多进程任务管理器

作用：
1. 不修改原来的 wechat_pc_moments_image_test.py
2. 每个账号启动一个独立 Python 进程
3. 每个账号独立日志
4. 子进程异常退出后自动重启
5. 支持一键停止所有多进程任务

注意：
这个文件只是“总控管理器”，不会帮你绕过微信多开限制。
前提是：你已经能在主机上正常打开多个微信窗口。
"""

import argparse
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# ============================================================
# 基础路径配置
# ============================================================

PYTHON_EXE = r"C:\Users\Deple\anaconda3\python.exe"

WORKDIR = Path(r"E:\wechat_like_helper\src")

ORIGINAL_SCRIPT = WORKDIR / "wechat_pc_moments_image_test.py"

RUNTIME_DIR = WORKDIR / "_wechat_multi_runtime"

LOG_DIR = WORKDIR / "logs" / "wechat_multi"


# ============================================================
# 账号配置
# ============================================================
# 先默认只开 A，确认多微信窗口已经准备好后，再改成 ["A", "B", "C"]
# 不然多个进程可能会同时抢同一个微信窗口。

DEFAULT_ACCOUNTS = ["A", "B", "C", "D"]


# 每个账号启动间隔，避免同时抢窗口
START_INTERVAL_SECONDS = 8

# 每隔多久检查一次子进程
CHECK_INTERVAL_SECONDS = 60

# 子进程退出后，多久重启
RESTART_DELAY_SECONDS = 10

# 每个账号 1 小时内最多重启次数，避免疯狂重启
MAX_RESTARTS_PER_HOUR = 5


# ============================================================
# 运行时间设置
# ============================================================
# 08:00 到 00:00 运行
# 00:00 到 08:00 自动停掉子进程，不再启动

ENABLE_TIME_WINDOW = True

RUN_START_HOUR = 8
RUN_END_HOUR = 0


# ============================================================
# 工具函数
# ============================================================

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_print(message: str) -> None:
    print(f"[{now_text()}] {message}", flush=True)


def ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def is_in_run_time() -> bool:
    """
    判断当前是否在允许运行时间内。
    当前默认：08:00 - 00:00
    """
    if not ENABLE_TIME_WINDOW:
        return True

    hour = datetime.now().hour

    if RUN_START_HOUR == RUN_END_HOUR:
        return True

    if RUN_START_HOUR < RUN_END_HOUR:
        return RUN_START_HOUR <= hour < RUN_END_HOUR

    # 例如 8 到 0，表示 08:00-23:59
    return hour >= RUN_START_HOUR or hour < RUN_END_HOUR


def create_worker_file(account: str) -> Path:
    """
    给每个账号生成一个独立 worker 文件。

    这样做的好处：
    1. 不改原始 py 文件
    2. 进程命令行里能看到 wechat_worker_A.py / B / C
    3. 停止时可以精准杀掉这些 worker
    """
    worker_path = RUNTIME_DIR / f"wechat_worker_{account}.py"

    worker_code = f'''# -*- coding: utf-8 -*-
"""
自动生成的 worker 文件。
账号：{account}

不要手动改这个文件。
真正逻辑仍然来自：
{ORIGINAL_SCRIPT}
"""

import os
import runpy
import sys
from pathlib import Path

ACCOUNT = "{account}"

WORKDIR = Path(r"{WORKDIR}")
ORIGINAL_SCRIPT = Path(r"{ORIGINAL_SCRIPT}")

os.environ["WECHAT_MULTI_ACCOUNT"] = ACCOUNT
os.environ["WECHAT_MULTI_WORKER"] = "1"

os.chdir(str(WORKDIR))

if str(WORKDIR) not in sys.path:
    sys.path.insert(0, str(WORKDIR))

print("=" * 80)
print(f"微信朋友圈点赞 worker 启动，账号={{ACCOUNT}}")
print(f"原始脚本：{{ORIGINAL_SCRIPT}}")
print("=" * 80)

runpy.run_path(str(ORIGINAL_SCRIPT), run_name="__main__")
'''

    worker_path.write_text(worker_code, encoding="utf-8")
    return worker_path


def open_log_file(account: str):
    today = datetime.now().strftime("%Y%m%d")
    log_path = LOG_DIR / f"wechat_like_account_{account}_{today}.log"

    log_file = open(
        log_path,
        "a",
        encoding="utf-8",
        buffering=1,
    )

    log_file.write("\n" + "=" * 80 + "\n")
    log_file.write(f"[{now_text()}] 启动账号 {account}\n")
    log_file.write("=" * 80 + "\n")

    return log_file, log_path


def start_account(account: str) -> dict:
    """
    启动单个账号进程。
    """
    worker_path = create_worker_file(account)
    log_file, log_path = open_log_file(account)

    log_print(f"[启动] 账号 {account}")
    log_print(f"[日志] {log_path}")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["WECHAT_MULTI_ACCOUNT"] = account

    process = subprocess.Popen(
        [
            PYTHON_EXE,
            "-X",
            "utf8",
            "-u",
            str(worker_path),
        ],
        cwd=str(WORKDIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )

    return {
        "account": account,
        "process": process,
        "log_file": log_file,
        "log_path": log_path,
        "worker_path": worker_path,
        "restart_times": [],
    }


def stop_process_item(item: dict) -> None:
    """
    停止一个子进程。
    """
    account = item.get("account")
    process = item.get("process")

    if process is None:
        return

    if process.poll() is not None:
        return

    log_print(f"[停止] 账号 {account}，PID={process.pid}")

    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass

    try:
        item["log_file"].close()
    except Exception:
        pass


def stop_all_tasks(tasks: dict) -> None:
    """
    停止所有子进程。
    """
    for account, item in list(tasks.items()):
        stop_process_item(item)

    tasks.clear()


def should_restart(item: dict) -> bool:
    """
    控制 1 小时内最多重启次数。
    """
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)

    restart_times = item.get("restart_times", [])
    restart_times = [
        t for t in restart_times
        if t > one_hour_ago
    ]

    item["restart_times"] = restart_times

    return len(restart_times) < MAX_RESTARTS_PER_HOUR


def record_restart(item: dict) -> None:
    item.setdefault("restart_times", []).append(datetime.now())


def parse_accounts(text: str) -> list:
    """
    解析账号参数。
    例如：
    A
    A,B
    A,B,C
    """
    result = []

    for part in text.split(","):
        part = part.strip().upper()
        if not part:
            continue
        result.append(part)

    return result


def kill_existing_multi_processes() -> None:
    """
    停止所有旧的多进程 worker 和 manager。

    只杀：
    1. wechat_worker_A.py / B / C
    2. wechat_multi_process_manager.py 的旧实例

    不会杀普通的 wechat_pc_moments_image_test.py，
    避免影响你原来的单账号脚本。
    """
    try:
        import psutil
    except Exception:
        log_print("[停止失败] 当前 Python 环境没有 psutil，无法自动查杀旧进程。")
        return

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

            is_worker = "wechat_worker_" in cmdline
            is_old_manager = (
                "wechat_multi_process_manager.py" in cmdline
                and pid != current_pid
            )

            if not is_worker and not is_old_manager:
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

    log_print(f"[完成] 已停止多进程相关任务数量：{killed}")


# ============================================================
# 主逻辑
# ============================================================

def run_manager(accounts: list) -> None:
    ensure_dirs()

    if not ORIGINAL_SCRIPT.exists():
        raise FileNotFoundError(
            f"找不到原始脚本：{ORIGINAL_SCRIPT}"
        )

    if not Path(PYTHON_EXE).exists():
        raise FileNotFoundError(
            f"找不到 Python：{PYTHON_EXE}"
        )

    log_print("=" * 80)
    log_print("微信朋友圈多进程任务管理器启动")
    log_print(f"工作目录：{WORKDIR}")
    log_print(f"原始脚本：{ORIGINAL_SCRIPT}")
    log_print(f"账号列表：{accounts}")
    log_print("=" * 80)

    tasks = {}

    try:
        while True:
            if not is_in_run_time():
                if tasks:
                    log_print("[时间控制] 当前不在运行时间内，停止所有账号任务。")
                    stop_all_tasks(tasks)

                log_print("[时间控制] 当前不在运行时间内，等待下一次检查。")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            # 启动缺失账号
            for account in accounts:
                if account not in tasks:
                    tasks[account] = start_account(account)
                    time.sleep(START_INTERVAL_SECONDS)

            # 检查账号进程状态
            for account, item in list(tasks.items()):
                process = item["process"]

                if process.poll() is None:
                    log_print(
                        f"[正常] 账号 {account} 正在运行，PID={process.pid}"
                    )
                    continue

                exit_code = process.returncode

                log_print(
                    f"[退出] 账号 {account} 已退出，ExitCode={exit_code}"
                )

                try:
                    item["log_file"].close()
                except Exception:
                    pass

                if not should_restart(item):
                    log_print(
                        f"[暂停重启] 账号 {account} 1小时内重启次数过多，暂不重启。"
                    )
                    del tasks[account]
                    continue

                record_restart(item)

                log_print(
                    f"[重启] 账号 {account} 将在 {RESTART_DELAY_SECONDS} 秒后重启。"
                )

                time.sleep(RESTART_DELAY_SECONDS)

                new_item = start_account(account)
                new_item["restart_times"] = item.get("restart_times", [])
                tasks[account] = new_item

            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        log_print("[手动停止] 收到 Ctrl+C，正在停止所有账号任务。")
        stop_all_tasks(tasks)

    except Exception as exc:
        log_print(f"[管理器异常] {exc}")
        stop_all_tasks(tasks)
        raise

    finally:
        stop_all_tasks(tasks)
        log_print("[结束] 微信朋友圈多进程任务管理器已退出。")


def main():
    parser = argparse.ArgumentParser(
        description="微信朋友圈多进程任务管理器"
    )

    parser.add_argument(
        "--accounts",
        type=str,
        default=",".join(DEFAULT_ACCOUNTS),
        help="要启动的账号，例如：A 或 A,B 或 A,B,C",
    )

    parser.add_argument(
        "--stop",
        action="store_true",
        help="停止所有多进程 worker 和旧 manager",
    )

    args = parser.parse_args()

    if args.stop:
        kill_existing_multi_processes()
        return

    accounts = parse_accounts(args.accounts)

    if not accounts:
        raise ValueError("账号列表为空，请使用 --accounts A,B,C")

    run_manager(accounts)


if __name__ == "__main__":
    main()