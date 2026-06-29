"""
剪贴板回调 —— 推手机 + 英文翻译弹窗。
"""
from ai_agent.container import get_container


async def clipboard_broadcast(data: dict):
    """剪贴板变化时：推手机。"""
    await get_container().mobile_pool.broadcast(data)
