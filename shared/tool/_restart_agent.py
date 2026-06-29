"""
AI Agent 重启脚本 — 独立进程执行（跨平台）。

用法：python _restart_agent.py <session_id>

流程：
  1. 写入 .restart_flag（session_id），供 main.py 启动后识别
  2. 查找占用 8007 端口的进程
  3. kill 进程
  4. 轮询确认端口释放
  5. 重新启动 main.py
  6. 健康检查：轮询确认新进程监听 8007（__main__ 块崩溃也能发现）

注：预验证已移至 schedule_restart 工具内部（restart_tool.py）。
独立进程脱离当前进程树（start_new_session），不会被 kill 误伤。
"""
import subprocess
import sys
import time
from pathlib import Path

from ai_agent.platform_utils import (
    get_python, get_pid_by_port, kill_process, get_project_root,
)

PORT = 8007

# 项目根目录
WORK = get_project_root()
FLAG_FILE = Path(WORK) / ".restart_flag"


def wait_port_free(port: int, timeout: int = 10) -> bool:
    """轮询等待端口释放，超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not get_pid_by_port(port):
            return True
        time.sleep(0.5)
    return False


def wait_port_taken(port: int, timeout: int = 10) -> bool:
    """轮询等待端口被监听，超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_pid_by_port(port):
            return True
        time.sleep(0.5)
    return False

def main():
    session_id = sys.argv[1] if len(sys.argv) > 1 else ""

    # 1. 写入重启标记
    if session_id:
        FLAG_FILE.write_text(session_id, encoding="utf-8")
        print(f"[INFO] 已写入 .restart_flag: session={session_id}")

    # 2. 找到占用端口的进程
    pids = get_pid_by_port(PORT)
    print(f"[INFO] 端口 {PORT} 占用进程: {pids}")

    if not pids:
        print("[WARN] 未找到占用端口的进程，直接启动")
        subprocess.Popen(
            [get_python(), "main.py"],
            cwd=str(WORK),
            start_new_session=True,
        )
        print("[OK] AI Agent 已启动")
        return

    # 4. kill 进程
    for pid in pids:
        print(f"[INFO] 正在终止 PID={pid}...")
        kill_process(pid)

    # 5. 等待端口释放
    if wait_port_free(PORT):
        print(f"[INFO] 端口 {PORT} 已释放")
    else:
        print(f"[WARN] 端口 {PORT} 未能在超时内释放，仍然尝试重启")

    time.sleep(1)

    # 6. 重新启动
    subprocess.Popen(
        [get_python(), "main.py"],
        cwd=str(WORK),
        start_new_session=True,
    )

    # 7. 健康检查：等待新进程监听端口（__main__ 块崩溃也能发现）
    if wait_port_taken(PORT, timeout=10):
        print(f"[OK] AI Agent 已重启 (killed={pids}, port={PORT} 监听正常)")
    else:
        print(f"[FATAL] 新进程已在后台启动但 {PORT} 端口 {10}s 内未监听！")
        print(f"[FATAL] 可能是 __main__ 块或 uvicorn 启动失败，请检查日志")


if __name__ == "__main__":
    main()
