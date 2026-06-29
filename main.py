"""
Memora — 跨设备 AI 助手 主入口（v4.0 OOP 重构）

项目文档（AI 修改代码前必读）：
    ARCHITECTURE.md  — 项目架构全景
    CONVENTIONS.md   — 编码规范（注释、重构、模块组织、审查清单）
"""
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from shared.tool._restart_agent import PORT
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from ai_agent.container import AppContainer, set_container, get_container
from ai_agent.clipboard_native import NativeClipboardListener
from ai_agent.clipboard_bridge import clipboard_broadcast
from ai_agent.ai_runner import _handle_restart
from ai_agent.http_routes import router as http_router
from ai_agent.middleware import auth_middleware, cache_middleware
from ai_agent import register_mcp_tools
from ai_agent.settings import settings
from shared.tool._reminder_scheduler import ReminderScheduler, set_scheduler
from ai_agent.frpc_watchdog import frpc_watchdog
import sys
from ai_agent.browser_session_manager import get_browser_session_manager

BASE_DIR = Path(__file__).parent
RESTART_FLAG = BASE_DIR / ".restart_flag"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 创建并初始化容器（DB + 工具加载 + 技能索引）──
    container = AppContainer()
    set_container(container)
    await container.init()

    # ── MCP 工具（独立后台连接，后续迁移到 ToolRegistry）──
    await register_mcp_tools()

    # ── 剪贴板监听（pyperclip 跨平台轮询 → clipboard_broadcast → 推手机）──
    monitor = NativeClipboardListener(clipboard_broadcast)
    container.session_mgr.clipboard_monitor = monitor
    monitor.start()

    # ── 提醒调度器（事件驱动 + 持久化，推送手机通知）──
    reminder_path = str(BASE_DIR / "reminders.json")
    reminder_scheduler = ReminderScheduler(reminder_path)
    set_scheduler(reminder_scheduler)
    await reminder_scheduler.start()

    # ── 远眺提醒（内置提醒，每 20 分钟弹 macOS 系统通知）──
    reminder_scheduler.start_eye_rest()

    # ── 浏览器会话管理器（定时清理空闲浏览器实例）──
    bsm = get_browser_session_manager()
    await bsm.start()

    # ── frpc 健康守护（仅 Windows，IP 变化时 frpc 偶发死循环）──
    if sys.platform == "win32":
        asyncio.create_task(frpc_watchdog())

    # ── 重启恢复：发送"重启完成。"到中断的会话 ──
    if RESTART_FLAG.exists():
        session_id = RESTART_FLAG.read_text(encoding="utf-8").strip()
        RESTART_FLAG.unlink()
        if session_id:
            asyncio.create_task(_handle_restart(session_id))

    try:
        yield
    finally:
        reminder_scheduler.stop_eye_rest()
        await bsm.stop()


app = FastAPI(lifespan=lifespan)

# ── 中间件 ──
app.middleware("http")(auth_middleware)
app.middleware("http")(cache_middleware)

# ── HTTP 路由 ──
app.include_router(http_router)


# ── WebSocket 端点 ──
async def ws_endpoint(ws: WebSocket):
    """密码验证 → MessageRouter 分发。

    两种连接类型：
        ?conn=global  → 手机全局连接（剪贴板 + 通知）
        其他          → 浏览器聊天连接（subscribe / chat / abort）
    """
    pwd = ws.query_params.get("password", "")
    conn_type = ws.query_params.get("conn", "")
    if pwd != settings.access_password:
        await ws.accept()
        await ws.send_json({"type": "error", "content": "密码错误"})
        await ws.close(code=4001, reason="密码错误")
        return
    await get_container().message_router.handle_connection(ws, conn_type)


app.add_api_websocket_route("/ws", ws_endpoint)

# ── 静态资源 ──
# /assets/ 优先匹配（带 hash 的 JS/CSS，长期缓存）
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "frontend-vue/dist/assets")), name="assets")
# 兜底：其他所有路径（favicon、index.html 等）
# nginx proxy_pass 尾部斜杠已剥离前缀（如 /home/ → /），
# 公网请求到达时路径不带前缀，由 / 挂载兜底。
# FastAPI 路由（/ws、/sessions 等）优先级高于静态挂载。
app.mount("/", StaticFiles(directory=str(BASE_DIR / "frontend-vue/dist")), name="root")

if __name__ == "__main__":
    import os as _os
    import sys as _sys
    if _os.environ.get("MEMORA_PREFLIGHT") == "1":
        # 预验证模式：完整模拟 lifespan 初始化（包括 tools 包加载），但不绑定端口
        import asyncio as _asyncio
        from ai_agent.container import AppContainer as _AppContainer
        _container = _AppContainer()
        set_container(_container)   # 必须在 init() 之前，tools 模块加载时 @tool 需要容器
        _asyncio.run(_container.init())
        print("[PREFLIGHT] OK — 所有模块（含 tools）加载正常")
        _sys.exit(0)
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, access_log=False,
                ws_ping_interval=30, ws_ping_timeout=10)
