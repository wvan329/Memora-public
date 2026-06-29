"""
工具注册中心 —— 替代全局 TOOLS / FUNC_TOOL_MAP / FUNC_SESSION_MAP。

提供 @tool 装饰器 + MCP 工具注册 + 调用分发。

v4.1：移除 bridge_old_globals() 桥接方法。工具注册现在直接通过
utils.py 的 @tool 装饰器和 mcp_tool.py 的 register_mcp_tool 写入 ToolRegistry。
旧 ai_config 全局变量（TOOLS/FUNC_TOOL_MAP/FUNC_SESSION_MAP）已删除。

v4.1.1：register_mcp_tool 支持去重替换 —— MCP 重连时同名工具自动覆盖而非追加，
修复"Tool names must be unique"错误。
"""
import asyncio
import importlib
import inspect
import json
import pkgutil

from pydantic import create_model

from ai_agent.settings import settings


class ToolRegistry:
    """工具注册中心：管理所有 AI 工具的注册、查找和调用。

    由 AppContainer 创建单例。所有工具（Python 本地 + MCP 远程）
    统一通过此注册中心对外提供。

    属性：
        _tools:       工具定义列表（OpenAI function calling 格式）
        _func_map:    {函数名: async wrapper}  — Python 本地工具
        _session_map: {函数名: MCP ClientSession} — MCP 远程工具
    """

    def __init__(self):
        self._tools: list[dict] = []
        self._func_map: dict[str, callable] = {}
        self._session_map: dict[str, object] = {}

    # ── 属性（供外部读取）──

    @property
    def tools(self) -> list[dict]:
        """工具定义列表，供 ai_response 传给 OpenAI API。"""
        return self._tools

    @property
    def func_map(self) -> dict:
        """Python 本地工具映射，供旧代码过渡期访问。"""
        return self._func_map

    @property
    def session_map(self) -> dict:
        """MCP 工具映射，供旧代码过渡期访问。"""
        return self._session_map

    # ── @tool 装饰器（新工具直接用此注册，无需经过 utils.py）──

    def tool(self, description: str):
        """装饰器：将 Python 函数注册为 AI 工具。

        自动从函数签名生成 Pydantic Model → OpenAI function calling schema，
        包装为异步调用（同步函数通过 asyncio.to_thread 执行）。

        Usage:
            @registry.tool("搜索网页内容")
            def web_search(query: str, max_results: int = 5) -> str:
                ...
        """
        def inner(func):
            sig = inspect.signature(func)
            fields = {}
            for name, param in sig.parameters.items():
                annotation = param.annotation
                if annotation == inspect._empty:
                    annotation = str
                default = ... if param.default == inspect._empty else param.default
                fields[name] = (annotation, default)

            pydantic_model = create_model(f"{func.__name__}Arguments", **fields)
            schema = pydantic_model.model_json_schema()

            self._tools.append({
                "type": "function",
                "function": {
                    "name": func.__name__,
                    "description": description,
                    "parameters": schema,
                }
            })

            async def wrapper(kwargs):
                data = pydantic_model(**kwargs)
                return await self._call_tool(func, **data.model_dump())

            self._func_map[func.__name__] = wrapper
            return func
        return inner

    # ── MCP 注册 ──

    def register_mcp_tool(self, session, tool_def: dict) -> None:
        """注册一个 MCP 远程工具，同名则替换（支持 MCP 重连）。

        为什么需要去重替换？
        MCP 连接断开后会自动重连，重连时会重新注册工具。
        如果不去重，TOOLS 列表中会出现同名工具，导致 API 返回
        "Tool names must be unique" 错误。替换策略确保 session 引用
        也更新为新的有效连接。

        Args:
            session:   MCP ClientSession（已初始化）
            tool_def:  OpenAI function calling 格式的工具定义
        """
        name = tool_def["function"]["name"]

        # 移除同名的旧工具定义和旧 session（MCP 重连场景）
        self._tools = [t for t in self._tools if t["function"]["name"] != name]
        self._session_map.pop(name, None)

        self._tools.append(tool_def)
        self._session_map[name] = session

    # ── 调用 ──

    async def _call_tool(self, func, *args, **kwargs):
        """统一调用入口：协程直接 await，同步函数通过 asyncio.to_thread 避免阻塞。"""
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            return await asyncio.to_thread(func, *args, **kwargs)

    async def invoke(self, func_name: str, arguments: dict) -> str:
        """按函数名 + 参数调用工具，返回结果字符串。

        先查 _func_map（Python 本地工具），再查 _session_map（MCP 远程工具）。

        Args:
            func_name: 工具名（如 "web_search"）
            arguments: 工具参数 dict（已从 JSON 解析）

        Returns:
            工具执行结果字符串。

        Raises:
            LookupError: 工具未注册。
        """
        # ── file_operation 缺 action 兜底 ──
        # AI 偶尔忘记传 action 参数（Pydantic 会直接拒绝），
        # 此处注入默认值 "read"，不修改 tool schema（AI 仍然看到 action=required）。
        warning = ""
        if func_name == "file_operation" and "action" not in arguments:
            arguments["action"] = "read"
            warning = "⚠️系统消息: action参数必须传,本次默认执行read操作,下次请传入action参数指定动作类型\n\n"

        if func_name in self._func_map:
            content = await self._func_map[func_name](arguments)
        elif func_name in self._session_map:
            session = self._session_map[func_name]
            # ── mermaid 兜底：强制 outputType 为 file/svg，防止 AI 幻觉传其他值 ──
            if func_name == "generate_mermaid_diagram":
                if arguments.get("outputType") not in ("file", "svg"):
                    arguments["outputType"] = "file"
            # MCP 调用加超时保护。MCP 服务器可能在网络层面半死不死
            # （TCP 连接未断但服务端不响应），无限等待会导致 AI 对话流整体卡住。
            # 30 秒足够正常的地图 API 调用完成，超时则返回错误信息让 AI 自行处理。
            try:
                result = await asyncio.wait_for(
                    session.call_tool(func_name, arguments), timeout=30)
            except asyncio.TimeoutError:
                content = "MCP 工具调用超时（30s），服务器可能无响应"
            else:
                content = result.content
                # MCP 返回 TextContent 列表，取第一个的 text
                if len(content) == 1:
                    try:
                        content = content[0].text
                    except Exception:
                        pass
                # ── mermaid 输出统一落到 local/temp/，避免散落根目录或大段代码撑爆上下文 ──
                if func_name == "generate_mermaid_diagram" and isinstance(content, str):
                    import os
                    import uuid
                    import shutil
                    stripped = content.strip()
                    dest_dir = os.path.join(os.getcwd(), "local", "temp")
                    os.makedirs(dest_dir, exist_ok=True)
                    if stripped.startswith("<svg"):
                        filepath = os.path.join(dest_dir, f"mermaid_{uuid.uuid4().hex[:8]}.svg")
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(content)
                        content = filepath
                    elif "saved to file:" in stripped:
                        # file 模式返回："Mermaid diagram saved to file: /path/to/file.png"
                        src = stripped.split("saved to file:", 1)[1].strip()
                        if os.path.isfile(src):
                            dest = os.path.join(dest_dir, os.path.basename(src))
                            shutil.move(src, dest)
                            content = dest
        else:
            raise LookupError(f"未知工具: {func_name}")

        return warning + self._ensure_str(content)

    @staticmethod
    def _ensure_str(content) -> str:
        """将任意类型内容转为字符串。

        str 直接返回；dict/list 通过 json.dumps；其他用 str()。
        """
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            return str(content)

    # ── 工具自动加载 ──

    def load_all_from_package(self, package_name: str = "tools") -> None:
        """自动导入指定包下所有模块。

        导入操作触发 @tool 装饰器副作用，将工具直接注册到当前 ToolRegistry 实例。
        """
        package = importlib.import_module(package_name)
        for _, name, _ in pkgutil.iter_modules(package.__path__):
            importlib.import_module(f"{package_name}.{name}")
