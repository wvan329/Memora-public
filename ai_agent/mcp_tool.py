"""
MCP 工具连接管理 —— 独立后台进程/HTTP/SSE 连接，将远程工具注册到 ToolRegistry。

v4.1 重构：直接注册到 ToolRegistry，不再经过 ai_config 的 TOOLS / FUNC_SESSION_MAP 全局变量。

v4.1.1 修复：
- config.pop("black"/"white") → config.get()，防止重连时黑/白名单丢失
- _convert_mcp_to_func_call 通过 registry.register_mcp_tool() 注册（自动去重替换）
"""
import asyncio
import os
import re
import subprocess
import sys
import httpx
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from .platform_utils import _find_node_bin_dir, resolve_win_node_cmd
from .settings import mcp_config


def _pkg_name(pkg):
    """去掉版本号：office-word==1.0 → office-word，@scoped/pkg@1.0 → @scoped/pkg"""
    pkg = re.sub(r"==.+$", "", pkg)  # PyPI 的 ==version
    pkg = re.sub(r"(?<=.)@.+$", "", pkg)  # npm 的 @version（保留开头的 @scope）
    return pkg


def _pre_install(config):
    """预安装 MCP 依赖，避免首次连接时下载超时。已安装则跳过。

    子进程需要找到 npm/npx → 从 platform_utils 探测 node bin 目录并注入 PATH。
    """
    cmd = config.get("command", "")
    args = config.get("args", [])

    pkg = None
    install_parts: list[str] | None = None

    if cmd == "uvx" and "--from" in args:
        idx = args.index("--from")
        if idx + 1 < len(args):
            pkg = args[idx + 1]
        install_parts = ["uv", "pip", "install"]

    elif cmd == "npx":
        for a in args:
            if not a.startswith("-"):
                pkg = a
                break
        if sys.platform == "win32":
            resolved = resolve_win_node_cmd("npm.cmd")
            if resolved:
                install_parts = [resolved[0], resolved[1], "install", "-g"]
            else:
                install_parts = ["npm.cmd", "install", "-g"]
        else:
            install_parts = ["npm", "install", "-g"]

    if not pkg or not install_parts:
        return

    name = _pkg_name(pkg)

    # 检查是否已安装
    if install_parts[0].startswith("uv"):
        check_cmd = ["pip", "show", name]
    else:
        if sys.platform == "win32":
            resolved = resolve_win_node_cmd("npm.cmd")
            if resolved:
                check_cmd = [resolved[0], resolved[1], "list", "-g", "--depth=0", name]
            else:
                check_cmd = ["npm.cmd", "list", "-g", "--depth=0", name]
        else:
            check_cmd = ["npm", "list", "-g", "--depth=0", name]

    # 构建包含 node bin 目录的环境变量，确保子进程能找到 npm/npx
    subprocess_env = os.environ.copy()
    node_bin = _find_node_bin_dir()
    if node_bin:
        subprocess_env["PATH"] = node_bin + os.pathsep + subprocess_env.get("PATH", "")

    r = subprocess.run(check_cmd, capture_output=True, env=subprocess_env)
    if r.returncode == 0:
        return

    try:
        subprocess.run([*install_parts, pkg], timeout=300, env=subprocess_env)
    except Exception as e:
        raise Exception(f"[MCP] 预安装失败: {e}")


async def _connect(client_factory, config, ready_event):
    _pre_install(config)
    # 浅拷贝防副作用：pop 移除 black/white 不影响调用方的 config dict。
    # 不 pop 的话 black/white 会作为意外关键字传给 client_factory(**config) 导致 TypeError。
    config = {**config}
    black = config.pop("black", None)
    white = config.pop("white", None)
    while True:
        try:
            async with client_factory(**config) as streams:
                read, write = streams[:2]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await register_mcp_tool(session, black, white)
                    ready_event.set()
                    while True:
                        await session.send_ping()
                        await asyncio.sleep(10)
        except Exception as e:
            await asyncio.sleep(10)


def stdio_factory(**config):
    """创建 stdio MCP 连接，自动注入 Node.js bin 目录到 PATH。

    fnm/nvm 等版本管理器通过 shell hook 动态注入 PATH，
    但 Python 子进程不经过 shell 初始化 → npx 可能不在 PATH 上。
    此处从 platform_utils._find_node_bin_dir() 探测并注入，
    确保 MCP npx 工具在子进程中可以找到。
    """
    node_bin = _find_node_bin_dir()
    if node_bin:
        env = config.get("env", {}) or {}
        if "PATH" not in env:
            # 在现有 PATH 基础上追加，而非覆盖
            existing_path = os.environ.get("PATH", "")
            env["PATH"] = node_bin + os.pathsep + existing_path
        config = {**config, "env": env}
    server = StdioServerParameters(**config)
    return stdio_client(server)


async def connect_stream(config, ready_event):
    # streamable_http_client 不接受 headers/auth，需要用 http_client 包装
    headers = config.pop("headers", None)
    auth = config.pop("auth", None)
    if headers or auth:
        config["http_client"] = httpx.AsyncClient(headers=headers, auth=auth)
    await _connect(streamable_http_client, config, ready_event)


async def connect_sse(config, ready_event):
    await _connect(sse_client, config, ready_event)


async def connect_std(config, ready_event):
    await _connect(stdio_factory, config, ready_event)


async def register_mcp_tools():
    """注册所有 MCP 工具（main.py lifespan 中调用）。

    后台启动所有 MCP 连接，等待最多 30 秒让首个连接就绪。
    超时后不阻塞服务启动——MCP 连接会在后台继续重试，
    成功后自动注册工具到 ToolRegistry。
    """
    ready_events = []

    for config in mcp_config.get("streamable_list", []):
        e = asyncio.Event()
        ready_events.append(e)
        asyncio.create_task(connect_stream(config, e))

    for config in mcp_config.get("sse_list", []):
        e = asyncio.Event()
        ready_events.append(e)
        asyncio.create_task(connect_sse(config, e))

    for config in mcp_config.get("stdio_list", []):
        e = asyncio.Event()
        ready_events.append(e)
        asyncio.create_task(connect_std(config, e))

    if not ready_events:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*(e.wait() for e in ready_events)),
            timeout=30)
    except asyncio.TimeoutError:
        print("[MCP] 部分连接未在 30s 内就绪，后台继续重试，"
              "服务已正常启动")


def _clean_schema(schema):
    """递归清理 MCP 输入 schema 中不需要的字段（title, examples）。"""
    if isinstance(schema, dict):
        schema.pop("title", None)
        schema.pop("examples", None)
        for v in schema.values():
            _clean_schema(v)
    elif isinstance(schema, list):
        for v in schema:
            _clean_schema(v)


# 把 MCP 的工具注册到 ToolRegistry 中
async def register_mcp_tool(session, black, white):
    """连接 MCP 服务器成功后注册其工具到 ToolRegistry。

    通过 registry.register_mcp_tool() 注册，该方法内置去重替换逻辑：
    同名工具自动覆盖旧定义和旧 session，支持 MCP 重连场景。
    """
    from .container import get_container
    registry = get_container().tool_registry

    tools = await session.list_tools()
    tools = tools.tools
    if white:
        tools = [t for t in tools if t.name in white]
    elif black:
        tools = [t for t in tools if t.name not in black]

    for t in tools:
        params = t.inputSchema
        _clean_schema(params)

        # mermaid 仅保留 file/svg，强制 file 兜底，避免 base64 撑爆上下文
        if t.name == "generate_mermaid_diagram":
            ot = params.get("properties", {}).get("outputType", {})
            ot["enum"] = ["file", "svg"]
            ot["default"] = "file"
        # 同步修正 outputType 参数中的 description（移除 base64/mermaid/svg_url/png_url）
        if t.name == "generate_mermaid_diagram":
            params['properties']['outputType']['description'] = (
                "The output type of the diagram. "
                "Can be 'file' (save PNG to disk) or 'svg' (save SVG to disk, returns file path). "
                "Default is 'file'."
            )

        tool_def = {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": params,
            }
        }
        # 通过 register_mcp_tool 注册，自动处理去重替换
        registry.register_mcp_tool(session, tool_def)