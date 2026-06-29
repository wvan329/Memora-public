"""
AI 对话调度 —— restart 恢复。
替代旧 ai_runner.py 中的 _run_and_broadcast。

重启恢复流程：
    1. 服务重启后检测 .restart_flag 文件
    2. 读取其中保存的 session_id
    3. 向该会话发送"重启完成。"消息
    4. 使用 ConversationTurn（与正常对话完全相同的代码路径）
"""
import asyncio
from ai_agent.permissions import parent_queue


async def _handle_restart(session_id: str):
    """服务重启后向被中断的会话发送恢复消息。

    与 MessageRouter._on_chat 中的 producer 逻辑一致：
    创建队列 → 设置 parent_queue → 启动消费者 → 执行 ConversationTurn。

    Args:
        session_id: .restart_flag 文件中保存的会话 ID（中断前的最后活跃会话）
    """
    from ai_agent.container import get_container
    c = get_container()

    if not c.session_mgr.try_claim(session_id):
        return  # 会话已被占用（极少发生，重启后不应有其他请求）

    task = asyncio.create_task(
        c.session_mgr.run_turn(session_id, "[系统提示] 已重启，如没有其他任务，直接回复重启完成即可。", "main",
                               c._create_conversation, parent_queue)
    )
    c.session_mgr.set_task(session_id, task)
