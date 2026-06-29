"""
手机通知推送工具 —— AI 可通过此工具向手机端发送自定义通知。
"""
import time
from typing import Annotated

from pydantic import Field

from ai_agent.utils import tool
from ai_agent.container import get_container


@tool("""推送通知消息到手机端。手机会弹出系统通知（类似微信消息）。
如果手机当前未连接（不在线），通知会静默丢弃，返回值会告知推送结果。""")
async def push_mobile_notification(
        content: Annotated[str, Field(description="通知正文内容")],
) -> str:
    count = await get_container().mobile_pool.broadcast({
        "type": "push_notification",
        "title": "Memora",
        "content": content,
        "timestamp": time.time(),
    })

    if count == 0:
        return "未推送：当前无手机连接。"
    elif count == 1:
        return f"已推送通知到 1 台设备。"
    else:
        return f"已推送通知到 {count} 台设备。"
