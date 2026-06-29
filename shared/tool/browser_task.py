from typing import Annotated
from pydantic import Field

from ai_agent.utils import tool
from ai_agent.container import get_container
from shared.tool._common import DelegateTask


@tool("使用浏览器执行调研任务。该工具自动打开浏览器→执行操作→返回结果。"
      "默认一次性清理（cleanup=True，任务完成后立即回收浏览器实例）；"
      "传 cleanup=False 则浏览器跨轮次存活，空闲10分钟后自动回收。"
      "遇到需要登录、验证码、人机验证等绕不过去的页面时，立即停止并告知用户，不要反复尝试或尝试绕过。"


      "支持批量并行：传入 tasks 列表可同时执行多个浏览器任务（各自独立浏览器实例），每个任务在前端显示为独立页面。"
      "单个任务传 tasks=[{\"task\": \"打开xxx\"}] 即可。"
      "可传 session_uuid 复用浏览器子AI的对话历史：tasks=[{\"task\": \"继续\", \"session_uuid\": \"已有UUID\"}]。")
async def browser_task(
        tasks: Annotated[list[DelegateTask], Field(
            description="浏览器任务列表，至少一个。每项：{task: 任务描述, session_uuid?: 会话UUID（空=新建，已有=复用历史）}"
        )],
        cleanup: Annotated[bool, Field(
            description="是否在任务完成后立即清理浏览器实例。默认True（一次性），设为False则浏览器保持存活，空闲10分钟后自动回收"
        )] = True,
):
    """浏览器任务工具。

    每个子任务独立浏览器实例。
    cleanup=True（默认）：任务完成后立即回收浏览器实例（一次性模式）。
    cleanup=False：浏览器跨轮次存活，空闲10分钟后自动回收。
    """
    if not tasks:
        return {"success": False, "result": "", "error": "tasks 不能为空"}
    items = [{"task": t["task"], "session_uuid": t.get("session_uuid", ""), "cleanup": cleanup} for t in tasks] if tasks else []
    return await get_container().sub_task_runner.run_batch(items, "browser_inner")
