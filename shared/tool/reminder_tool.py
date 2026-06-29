"""
提醒相关 AI 工具 —— 纯工具函数，核心调度逻辑在 ai_agent.reminder_scheduler。
"""

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from ai_agent.utils import tool
from shared.tool._reminder_scheduler import get_scheduler


@tool("""添加手机定时提醒。到指定时间后，Memora 会通过手机通知推送提醒消息。
每次调用此工具前，必须调用一次get_current_time工具获取当下准确时间。
支持每日重复提醒（如"每天9:30起床"）和一次性提醒（如"明天下午3点开会"）。
如需修改已有提醒，请先调用 remove_reminder 删除，再重新添加。""")
async def add_reminder(
        id: Annotated[str, Field(description="提醒唯一标识，如 'wakeup'、'meeting_1'。修改已有提醒时使用相同 id")],
        time: Annotated[str, Field(description="提醒时间，24小时制格式 HH:MM，如 '09:30'、'15:00'")],
        message: Annotated[str, Field(description="提醒内容，推送通知的正文")],
        type: Annotated[Literal["daily", "once"], Field(description="'daily' = 每日重复，'once' = 一次性")] = "daily",
        date: Annotated[str | None, Field(description="一次性提醒的日期，格式 YYYY-MM-DD，如 '2026-07-14'。仅 type='once' 时有效，daily 忽略此字段")] = None,
) -> str:
    scheduler = get_scheduler()
    reminder = {"id": id, "time": time, "message": message, "type": type}
    if date:
        reminder["date"] = date
    return await scheduler.add(reminder)


@tool("""删除一个手机定时提醒。取消后台等待任务并从存储中移除。""")
async def remove_reminder(
        id: Annotated[str, Field(description="要删除的提醒ID")],
) -> str:
    return await get_scheduler().remove(id)


@tool("""列出当前所有手机定时提醒，包括每日重复和一次性的。返回每个提醒的id、时间、内容、类型、启用状态。""")
async def list_reminders() -> str:
    reminders = get_scheduler().list_all()
    if not reminders:
        return "当前没有设置任何提醒。"
    lines = []
    for r in reminders:
        type_label = "每日" if r.get("type") == "daily" else "一次性"
        status = "✅" if r.get("enabled", True) else "❌"
        date_str = f" {r['date']}" if r.get("date") else ""
        last = f" (上次: {r['last_date']})" if r.get("last_date") else ""
        lines.append(f"{status} [{r['id']}] {r['time']}{date_str} ({type_label}) — {r['message']}{last}")
    return "\n".join(lines)


@tool("""获取当前日期和时间。返回日期（YYYY-MM-DD）、时间（HH:MM:SS）、星期几、Unix 时间戳。
AI 在设置一次性提醒时，应先用此工具获取当前日期，再根据用户说的"明天""后天""下周一"等计算出正确的日期。""")
async def get_current_time() -> str:
    now = datetime.now()
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return json.dumps({
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": weekday_map[now.weekday()],
        "timestamp": int(now.timestamp()),
    }, ensure_ascii=False)
