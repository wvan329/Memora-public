from typing import Annotated

from pydantic import Field

from ai_agent.utils import tool
from ai_agent.container import get_container
from shared.tool._common import DelegateTask


@tool("""
委托子AI执行独立任务，子ai拥有所有独立的工具。返回结构化结果。适用于可拆分的子任务。

- tasks: 任务列表，至少一个。每项含 task（任务描述）+ session_uuid（可选，空则自动新建）
  单个任务示例：tasks=[{"task": "分析项目"}]
  复用历史示例：tasks=[{"task": "继续分析", "session_uuid": "已有UUID"}]
  批量并行示例：tasks=[{"task": "任务A"}, {"task": "任务B", "session_uuid": "已有UUID"}]
""")
async def ai_delegate(
        tasks: Annotated[list[DelegateTask], Field(
            description="任务列表，至少一个。每项：{task: 任务描述, session_uuid?: 会话UUID（空=新建）}"
        )],
):
    """委托子AI执行任务。每项的 session_uuid 为可选，空字符串则自动新建。"""
    if not tasks:
        return {"success": False, "result": "", "error": "tasks 不能为空"}
    # Pydantic model → dict for run_sub_task_batch
    items = [{"task": t["task"], "session_uuid": t.get("session_uuid", "")} for t in tasks] if tasks else []
    return await get_container().sub_task_runner.run_batch(items, "delegate")
