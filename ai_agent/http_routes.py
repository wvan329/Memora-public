"""
HTTP 路由 —— 登录、会话管理、文件下载、首页。

v4.1：统一通过 get_container().repo 访问数据库，不再混用 sqlite 薄包装。
"""
import mimetypes
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from ai_agent.settings import settings

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent.parent  # ai_agent/ → Memora/

# 便捷访问：所有路由通过此函数获取 repo，避免每个函数内重复 get_container()
def _repo():
    from ai_agent.container import get_container
    return get_container().repo


class LoginRequest(BaseModel):
    password: str


@router.post("/api/login")
async def api_login(req: LoginRequest):
    """前端登录：验证密码，成功后浏览器存 localStorage。"""
    if not settings.access_password:
        return JSONResponse(status_code=401, content={"error": "密码错误"})
    if req.password != settings.access_password:
        return JSONResponse(status_code=401, content={"error": "密码错误"})
    return {"ok": True}


@router.get("/")
async def index():
    """根路径 → 前端 SPA。"""
    # 新版（Vue 3 重构，当前使用）：
    return FileResponse(str(BASE_DIR / "frontend-vue/dist/index.html"))
    # 旧版（注释以备回退）：
    # return FileResponse(str(BASE_DIR / "frontend/index.html"))


@router.get("/api/download")
async def api_download(path: str):
    """文件下载：AI 生成的报告、图片等通过此接口提供给浏览器。"""
    file_path = Path(unquote(path)).resolve()
    if not file_path.exists():
        return JSONResponse(status_code=404, content={"error": f"文件不存在: {path}"})
    if not file_path.is_file():
        return JSONResponse(status_code=400, content={"error": f"不是文件: {path}"})
    mime, _ = mimetypes.guess_type(str(file_path))
    return FileResponse(str(file_path), filename=file_path.name, media_type=mime or "application/octet-stream")


@router.get("/sessions")
async def get_sessions(offset: int = 0, limit: int = 50):
    """左侧栏会话列表（分页）。
    
    返回：
        pinned: 所有置顶会话（不受分页限制，按 pinned_at 升序）
        sessions: 非置顶会话（按 last_time 降序，分页）
        has_more: 是否还有更多非置顶会话
    """
    repo = _repo()
    pinned = await repo.get_pinned_sessions()
    unpinned, has_more = await repo.get_unpinned_sessions(limit, offset)
    # 为每个会话计算展示标题：优先 custom_title，否则用第一条用户消息
    def _display(s):
        return s.get("custom_title") or s.get("title") or "(空会话)"
    for s in pinned:
        s["display_title"] = _display(s)
    for s in unpinned:
        s["display_title"] = _display(s)
    return {"pinned": pinned, "sessions": unpinned, "has_more": has_more}


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """删除整个会话。"""
    await _repo().delete_session(session_id)
    return {"ok": True}


class PinRequest(BaseModel):
    pinned: bool


class RenameRequest(BaseModel):
    title: str = ""


@router.put("/sessions/{session_id}/pin")
async def toggle_pin(session_id: str, req: PinRequest):
    """切换会话置顶状态。"""
    await _repo().set_pinned(session_id, req.pinned)
    return {"ok": True}


@router.put("/sessions/{session_id}/rename")
async def rename_session(session_id: str, req: RenameRequest):
    """重命名会话。空标题视为清除自定义标题，回退到默认行为。"""
    await _repo().set_custom_title(session_id, req.title)
    return {"ok": True}


@router.delete("/turns/{session_id}/{turn_id}")
async def delete_turn_endpoint(session_id: str, turn_id: str):
    """删除某一轮对话（用户消息 + AI 回复 + 工具调用）。"""
    await _repo().delete_turn(session_id, turn_id)
    return {"ok": True}


class ForkRequest(BaseModel):
    session_id: str
    turn_id: str


class NotificationRequest(BaseModel):
    enabled: bool


class VisionHighResRequest(BaseModel):
    enabled: bool


class BrowserHeadedRequest(BaseModel):
    enabled: bool


@router.get("/api/notification")
async def get_notification():
    """获取通知开关状态。"""
    from shared.tool.toggle_notification import is_notification_enabled
    return {"enabled": is_notification_enabled()}


@router.post("/api/notification")
async def set_notification(req: NotificationRequest):
    """切换通知开关。"""
    from ai_agent.settings import settings, set_env_bool
    settings.notification_enabled = req.enabled
    set_env_bool('NOTIFICATION_ENABLED', req.enabled)
    return {"enabled": req.enabled}


@router.get("/api/vision-high-res")
async def get_vision_high_res():
    """获取图片识别高精度默认值。"""
    from ai_agent.settings import settings
    return {"enabled": settings.vision_high_res_default}


@router.post("/api/vision-high-res")
async def set_vision_high_res(req: VisionHighResRequest):
    """切换图片识别高精度默认值，持久化到 .env。"""
    from ai_agent.settings import settings, set_env_bool
    settings.vision_high_res_default = req.enabled
    set_env_bool('VISION_HIGH_RES_DEFAULT', req.enabled)
    return {"enabled": req.enabled}


@router.get("/api/browser-headed")
async def get_browser_headed():
    """获取浏览器是否显示窗口。"""
    from ai_agent.settings import settings
    return {"enabled": settings.browser_headed}


@router.post("/api/browser-headed")
async def set_browser_headed(req: BrowserHeadedRequest):
    """切换浏览器是否显示窗口，持久化到 .env。"""
    from ai_agent.settings import settings, set_env_bool
    settings.browser_headed = req.enabled
    set_env_bool('BROWSER_HEADED', req.enabled)
    return {"enabled": req.enabled}


@router.post("/api/fork")
async def api_fork(req: ForkRequest):
    """
    分叉会话：复制指定 turn_id 及之前的所有消息到新会话（包含该轮对话本身）。
    返回 { new_session_id }。
    """
    repo = _repo()
    # 加载原会话全部消息
    messages = await repo.load(req.session_id)

    # 找到目标 turn_id 的位置，截取之前的所有消息
    cutoff_index = None
    for i, msg in enumerate(messages):
        if msg.get("turn_id") == req.turn_id:
            cutoff_index = i
            break

    if cutoff_index is None:
        return JSONResponse(status_code=404, content={"error": f"未找到 turn_id: {req.turn_id}"})

    # 找到该轮结束位置（相同 turn_id 的最后一条）
    end_index = cutoff_index
    while end_index + 1 < len(messages) and messages[end_index + 1].get("turn_id") == req.turn_id:
        end_index += 1

    # 截取该 turn 及之前的所有消息（含用户消息本身）
    messages_before = messages[:end_index + 1]
    if len(messages_before) <= 1:
        return JSONResponse(status_code=400, content={"error": "该轮对话没有可复制的内容"})

    # 新 session_id
    import uuid
    new_sid = str(uuid.uuid4())

    # 复制到新会话（批量写入，事务保护）
    batch = []
    for msg in messages_before:
        msg_copy = {**msg}
        msg_copy["user_id"] = new_sid
        batch.append(msg_copy)
    await repo.save_batch(batch)

    return {"new_session_id": new_sid}


@router.get("/api/welcome")
async def api_welcome():
    """空会话封面：流式返回一句有灵性的话（flash 模型，非思考模式）。"""
    from ai_agent.ai_config import ai_chat

    async def generate():
        async for chunk in ai_chat(
            "说一句平凡却让人停下来想一秒的话。不要解释，不要用引号。",
            enable_thinking=False,
            model=settings.flash_model_name,
        ):
            if chunk["type"] == "text":
                yield f"data: {chunk['content']}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 调试端点 ──

@router.get("/api/debug/pool")
async def debug_pool():
    """调试：列出 MobileConnectionPool 中所有连接的来源路径。"""
    from ai_agent.container import get_container
    clients = get_container().mobile_pool.debug_info()
    return {"count": len(clients), "clients": clients}
