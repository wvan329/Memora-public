"""
工具函数集合 —— 向后兼容薄包装。

tools/ 模块通过此文件导入 @tool 装饰器和其他辅助函数。

v4.1 重构：@tool 装饰器直接注册到 ToolRegistry（通过容器），
不再经过 ai_config 的旧全局变量 TOOLS / FUNC_TOOL_MAP。

v4.2 跨平台改造：
    - 硬编码路径 → sys.prefix / sys.executable（platform_utils）
    - taskkill → os.kill（platform_utils）
    - @tool 装饰器 → 委托给 ToolRegistry.tool()（消除重复实现）
    - PATH 拼接 → os.pathsep + sysconfig（platform_utils）
"""
import asyncio
import inspect
import json
import os
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# ContextVars —— 统一由 permissions.py 定义，此处导入以保持向后兼容
# ═══════════════════════════════════════════════════════════════

from ai_agent.permissions import current_session_id  # noqa: F401, E402

# ═══════════════════════════════════════════════════════════════
# 环境变量（供 shell_tool 使用）
# ═══════════════════════════════════════════════════════════════

# 跨平台：platform_utils 自动适配 PATH 分隔符（Windows=; macOS/Linux=:）
# 和可执行文件目录名（Windows=Scripts macOS/Linux=bin）
from ai_agent.platform_utils import build_path_env, get_python_prefix  # noqa: E402

env = os.environ.copy()
env["PATH"] = build_path_env()
env["CONDA_PREFIX"] = get_python_prefix()
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"

# ═══════════════════════════════════════════════════════════════
# 纯工具函数
# ═══════════════════════════════════════════════════════════════


def get_path(path: str) -> Path:
    return Path(path).absolute()


def safe_decode(data: bytes) -> str:
    """多编码尝试解码字节，兜底用 UTF-8 replace。"""
    for enc in ["utf-8", "gbk", "gb2312"]:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")


# ensure_str 和 call_tool 已移除（死代码，实际调用方均使用 ToolRegistry 内对应方法）

async def kill_proc(p: int):
    """终止进程（委托给 platform_utils.kill_process）。

    保留 async 签名以兼容所有现有调用方（shell_tool.py 等）。
    """
    from ai_agent.platform_utils import kill_process
    kill_process(p)


# ═══════════════════════════════════════════════════════════════
# @tool 装饰器 —— 委托给 ToolRegistry.tool()
# ═══════════════════════════════════════════════════════════════

def tool(description: str):
    """装饰器：将函数注册为 AI 工具。

    直接委托给 ToolRegistry.tool() —— 唯一的工具注册实现。
    tools/ 下所有模块通过此函数注册，导入路径不变。
    """
    from ai_agent.container import get_container
    return get_container().tool_registry.tool(description)
