"""
上下文压缩工具。

将主会话的历史写入子 AI 上下文，由子 AI 直接阅读并生成摘要，
无需手动拼接 prompt。压缩后保留 system 提示词 + AI 摘要 + 最后 5 轮对话。
"""
from ai_agent.utils import tool, current_session_id
from ai_agent.container import get_container
import json
import uuid


_KEEP_TURNS = 5


@tool(f"压缩当前会话上下文。保留系统提示词 + 最近 {_KEEP_TURNS} 轮对话 + AI 摘要。"
      "调用后当前轮立即终止，不产生额外历史记录。")
async def compress_context():
    sid = current_session_id.get()
    if not sid:
        return "[错误: 无法获取当前会话 ID]"

    repo = get_container().repo
    messages = await repo.load(sid)
    if not messages:
        return "[当前会话无消息]"

    # 1. 分离 system + 按 turn_id 分组
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    turn_order = []
    for m in reversed(non_system):
        tid = m.get("turn_id", "")
        if tid and tid not in turn_order:
            turn_order.append(tid)

    keep_turns = set(turn_order[:_KEEP_TURNS])
    old_msgs = [m for m in non_system if m.get("turn_id") not in keep_turns]
    keep_msgs = [m for m in non_system if m.get("turn_id") in keep_turns]

    if not old_msgs:
        return f"[无需压缩: 仅有 {len(turn_order)} 轮对话]"

    # 2. 把旧消息写入子 AI 上下文（让其直接阅读，无需拼接 prompt）
    sub_sid = str(uuid.uuid4())
    sub_msgs = [{**m, "user_id": sub_sid, "id": None} for m in old_msgs]
    await repo.save_batch(sub_msgs)

    # 3. 子 AI 直接基于上下文生成摘要
    prompt = (
        "请以「回顾」的口吻，总结我们上面做了哪些事情。"
        "不要调用任何工具，你直接生成回复。"
        "按时间线逐轮记录：用户问了什么、你做了什么（工具/决策）、结果如何。"
        "保留具体细节（文件路径、命令、错误、关键代码）。标注未完成事项。"
        "信息密度优先，不要遗漏。"
    )
    batch_result = await get_container().sub_task_runner.run_batch([{"task": prompt, "session_uuid": sub_sid}], "compress")
    # run_sub_task_batch 返回 {"success": bool, "sessions": [...]}
    first_session = batch_result.get("sessions", [{}])[0] if batch_result.get("sessions") else {}
    summary = first_session.get("result", "").strip() or "对话历史摘要"

    summary_msg = {
        "role": "system",
        "content": f"[对话摘要] {summary}",
        "user_id": sid,
        "turn_id": "",
    }

    # 4. 原子替换主会话：system + 摘要 + 最后 5 轮
    new_messages = system_msgs + [summary_msg] + keep_msgs
    await repo.replace_session_messages(sid, new_messages)
    await repo.delete_session(sub_sid)


    return json.dumps({
        "success": True,
        "sessions": batch_result.get("sessions", []),
        "summary": f"✅ 压缩完成。保留 {len(system_msgs)} 条系统提示词 + 1 条 AI 摘要 + 最后 {_KEEP_TURNS} 轮对话。"
    }, ensure_ascii=False)
