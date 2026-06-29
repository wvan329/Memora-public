"""
跨平台工具函数 —— 所有平台差异集中在此模块，对外暴露统一接口。

设计原则：
    不使用 if os.name / sys.platform 分支。
    所有差异靠 Python 标准库 + psutil 的跨平台抽象自动处理。
    本模块由 AppContainer 不依赖，其他模块可直接 import。

替代的旧实现：
    - 硬编码路径  D:\\soft\\...      → sys.executable / sys.prefix
    - taskkill /F /PID xxx           → os.kill(pid, SIGKILL)
    - netstat -ano | findstr :PORT   → psutil.net_connections()
    - CREATENEWPROCESSGROUP          → start_new_session=True
    - PATH 用 ; 拼接                 → os.pathsep
"""
import os
import shutil
import signal
import subprocess
import sys
import sysconfig
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# Node.js .cmd 绕过：Windows 上直接用 node.exe + cli.js
# ═══════════════════════════════════════════════════════════════
# 根因：Windows CreateProcess 检测到 .cmd 文件 → 强制走 cmd.exe
# → URL 中的 & | < > 被 shell 解析。直接用 node.exe 调用对应的
# cli.js 可彻底规避，因为 node.exe 是 .exe → 不经过 cmd.exe。
_CMD_TO_CLI: dict[str, str] = {
    "npx.cmd": "node_modules/npm/bin/npx-cli.js",
    "npm.cmd": "node_modules/npm/bin/npm-cli.js",
}


def resolve_win_node_cmd(cmd_name: str) -> list[str] | None:
    """Windows：绕过 .cmd 文件，返回 [node.exe, cli.js] 直接调用。

    Node.js 发行版结构约定：npx.cmd/npm.cmd/node.exe/node_modules/
    四者在同一父目录下。直接用 node.exe + cli.js 可彻底避开
    cmd.exe 对参数中 & | < > 等特殊字符的解析。

    Args:
        cmd_name: "npx.cmd" 或 "npm.cmd"

    Returns:
        ["D:/.../node.exe", "D:/.../npm-cli.js"] 或 None（降级回 .cmd）
    """
    if cmd_name not in _CMD_TO_CLI:
        return None

    cmd_path = shutil.which(cmd_name)
    if not cmd_path:
        return None
    cmd_dir = Path(cmd_path).parent

    # node.exe：优先同目录，降级到 PATH
    node_exe = cmd_dir / "node.exe"
    if not node_exe.is_file():
        found = shutil.which("node")
        if not found:
            return None
        node_exe = Path(found)

    # cli.js：通过映射表查找
    cli_rel = _CMD_TO_CLI[cmd_name]
    cli_js = cmd_dir / cli_rel
    if not cli_js.is_file():
        return None

    return [str(node_exe), str(cli_js)]


def get_python() -> str:
    """当前 Python 解释器的完整路径（跨平台）。"""
    return sys.executable


def get_python_prefix() -> str:
    """当前 Python 环境的根目录（跨平台）。"""
    return sys.prefix


def kill_process(pid: int) -> None:
    """终止指定 PID 的进程（跨平台）。

    不杀子进程树——本项目所有后台进程都用 start_new_session=True 启动，
    已处于独立进程组中，不存在需要级联终止的父子关系。
    """
    # Windows 没有 POSIX 信号，用 taskkill
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=10,
        )
        return

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # 进程已不存在


def get_pid_by_port(port: int) -> list[int]:
    """获取占用指定端口的进程 PID 列表（跨平台）。

    Args:
        port: TCP 端口号

    Returns:
        PID 列表（去重），端口未被占用时返回空列表。
    """
    # Windows：netstat -ano 零依赖，不需要管理员权限即可获取 PID
    if sys.platform == "win32":
        return _get_pid_by_port_netstat(port)

    # Unix：lsof 优先，psutil 回退
    # lsof 不受 macOS SIP 限制；psutil.net_connections() 在 macOS 上会触发
    # AccessDenied（SIP 保护），导致整个重启脚本崩溃。
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = [int(pid) for pid in result.stdout.strip().split()]
            return list(set(pids))
        return []
    except Exception:
        pass

    # lsof 不可用时的回退方案（psutil）
    try:
        import psutil
    except ImportError:
        return []
    pids = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.status == "LISTEN":
                if conn.pid:
                    pids.add(conn.pid)
    except Exception:
        pass
    return list(pids)


def _get_pid_by_port_netstat(port: int) -> list[int]:
    """Windows：通过 netstat -ano 获取占用端口的 PID。

    netstat 是 Windows 原生命令，零依赖，不需要管理员权限。
    输出格式固定（-ano = 数字地址 + PID），不受系统语言影响。
    """
    result = subprocess.run(
        ["cmd", "/c", f"netstat -ano | findstr :{port}"],
        capture_output=True, timeout=5,
    )
    # Windows netstat 输出为 GBK 编码
    stdout = result.stdout.decode("gbk", errors="replace")
    pids = set()
    for line in stdout.splitlines():
        # 格式：TCP    0.0.0.0:8007    0.0.0.0:0    LISTENING    12345
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(int(parts[-1]))
    return list(pids)


def _find_node_bin_dir() -> str:
    """探测 Node.js 的 bin 目录（npm/npx 所在目录），找不到返回空字符串。

    按优先级探测：
        1. fnm (Fast Node Manager) — 默认别名
        2. fnm multishell — 当前 shell 的临时链接
        3. nvm (Node Version Manager) — 当前版本
        4. volta — 当前版本
        5. 系统路径：/usr/local/bin, /opt/homebrew/bin

    为什么需要这个函数？
    fnm/nvm 等版本管理器通过 shell hook 动态注入 PATH，
    但 Python 子进程不经过 shell 初始化 → node/npm/npx 不在 PATH 上。
    此函数确保 MCP 工具（npx）在子进程中可以找到。
    """
    home = Path.home()

    # fnm: 默认别名
    fnm_default = home / ".local/share/fnm/aliases/default/bin"
    if (fnm_default / "node").exists() or (fnm_default / "node.exe").exists():
        return str(fnm_default)

    # fnm: multishell 临时链接（当前 shell 激活的版本）
    fnm_multishell_dir = home / ".local/state"
    if fnm_multishell_dir.exists():
        for d in sorted(fnm_multishell_dir.glob("fnm_multishells/*/bin"), reverse=True):
            if (d / "node").exists() or (d / "node.exe").exists():
                return str(d)

    # nvm-windows (Windows)：NVM_SYMLINK 指向当前 node 版本目录
    nvm_symlink = os.environ.get("NVM_SYMLINK", "")
    if nvm_symlink and (Path(nvm_symlink) / "node.exe").exists():
        return nvm_symlink

    # nvm (Unix)
    nvm_dir = os.environ.get("NVM_DIR", "")
    if not nvm_dir:
        nvm_dir = str(home / ".nvm")
    nvm_current = Path(nvm_dir) / "versions/node"
    if nvm_current.exists():
        # 取最新版本
        versions = sorted(nvm_current.glob("*"), reverse=True)
        for v in versions:
            bin_dir = v / "bin"
            if (bin_dir / "node").exists() or (bin_dir / "node.exe").exists():
                return str(bin_dir)

    # volta
    volta_dir = home / ".volta/bin"
    if (volta_dir / "node").exists() or (volta_dir / "node.exe").exists():
        return str(volta_dir)

    # 系统路径（Homebrew / 官方安装器）
    for sys_dir in ["/opt/homebrew/bin", "/usr/local/bin"]:
        p = Path(sys_dir)
        if (p / "node").exists() or (p / "node.exe").exists():
            return sys_dir

    return ""


def build_path_env() -> str:
    """构建包含当前 Python 环境和 Node.js 的 PATH 字符串（跨平台）。

    使用 os.pathsep 自动适配分隔符（Windows=;  macOS/Linux=:），
    使用 sysconfig.get_path('scripts') 自动适配可执行文件目录名
    （Windows=Scripts  macOS/Linux=bin）。

    额外探测 Node.js bin 目录（fnm/nvm/volta/系统安装），
    确保 MCP 工具（npx）在子进程中可用。
    """
    scripts_dir = sysconfig.get_path("scripts")
    parts = [scripts_dir, sys.prefix]

    # 探测 Node.js bin 目录，确保 npx 可用
    node_bin = _find_node_bin_dir()
    if node_bin:
        parts.append(node_bin)

    parts.append(os.environ.get("PATH", ""))
    return os.pathsep.join(parts)


def start_detached(cmd: list[str], cwd: str | None = None) -> subprocess.Popen:
    """以独立进程组启动命令（跨平台）。

    start_new_session=True 在 POSIX 上调用 setsid()，
    在 Windows 上等效于 CREATE_NEW_PROCESS_GROUP。
    """
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def show_desktop_popup(text: str, seconds: int = 20):
    """桌面弹窗（跨平台）。macOS 用原生对话框，Windows/Linux 用 tkinter。

    Args:
        text: 弹窗文字内容
        seconds: 自动关闭秒数
    """
    if sys.platform == "darwin":
        _show_macos_popup(text, seconds)
    else:
        _show_tkinter_popup(text, seconds)


def _show_macos_popup(text: str, seconds: int):
    """macOS 原生对话框。giving up after 实现自动关闭。"""
    subprocess.run([
        "osascript", "-e",
        f'display dialog "{text}" with title "Memora" '
        f'buttons {{"知道了"}} default button 1 giving up after {seconds}'
    ], capture_output=True)


def _show_tkinter_popup(text: str, seconds: int):
    """Windows/Linux tkinter 弹窗。无边框置顶，居中，自动关闭。"""
    import tkinter as tk
    import threading

    def _show():
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#1e1e1e")

        frame = tk.Frame(root, bg="#1e1e1e", padx=24, pady=16)
        frame.pack()

        tk.Label(
            frame, text=text, fg="white", bg="#1e1e1e",
            font=("Microsoft YaHei", 14), wraplength=400,
        ).pack()

        tk.Button(
            frame, text="知道了", command=root.destroy,
            bg="#444", fg="white", relief="flat", padx=24, pady=6,
            font=("Microsoft YaHei", 12),
        ).pack(pady=(12, 0))

        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        root.after(seconds * 1000, root.destroy)
        root.mainloop()

    threading.Thread(target=_show, daemon=True).start()


def get_project_root() -> Path:
    """获取项目根目录的绝对路径。

    从本文件位置向上两级（ai_agent/platform_utils.py → ai_agent/ → 项目根）。
    """
    return Path(__file__).resolve().parent.parent