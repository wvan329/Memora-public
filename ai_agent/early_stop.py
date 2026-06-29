"""
AI 工具调用的提前终止处理（restart / compress）。

协议说明：
某些工具（如 schedule_restart、compress_context）需要中断正常的 AI 对话循环。
它们通过在工具返回的 JSON 中插入特殊标记字段来实现这一目的：

    {"__restart__": true, "summary": "..."}

ConversationTurn._execute_tools 在所有工具并行执行完后，
通过 _detect_early_stop 扫描结果中是否包含这些标记。
如果检测到，则调用 _handle_early_stop 执行相应的终止逻辑，
并跳过后续的 AI 调用轮次。

为什么使用 JSON 魔术字段而不是异常机制？
因为工具调用是并行执行的（asyncio.gather），异常会取消其他工具。
魔术字段协议允许所有工具正常完成，然后在结果中声明"需要提前终止"。

当前支持的标记：
    __restart__  — 重启 AI Agent 服务（由 schedule_restart 工具触发）
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

# 提前终止标记集合。工具返回的 JSON 中若包含任一字段且值为 truthy，
# 则触发提前终止流程。
_EARLY_STOP_FLAGS = {"__restart__"}


def _detect_early_stop(results: list[dict]) -> dict | None:
    """检测工具结果中是否有提前终止标记。

    遍历所有工具结果，尝试将 content 解析为 JSON，
    检查是否包含 _EARLY_STOP_FLAGS 中的任何字段。
    如果检测到，清洗掉标记字段（只保留 clean_content），
    返回终止信息 dict。
    """
    for r in results:
        content = r.get("content", "")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    for flag in _EARLY_STOP_FLAGS:
                        if parsed.get(flag):
                            clean = parsed.get("summary") or parsed.get("content") or ""
                            r["content"] = clean
                            return {"flag": flag, "clean_content": clean, **parsed}
            except (json.JSONDecodeError, TypeError):
                pass
    return None


async def _handle_early_stop(early_stop: dict, session_id: str, q):
    """处理提前终止：restart 启动独立脚本，然后结束当前流。

    restart 流程：
    1. schedule_restart 工具内部已完成预验证（失败则不会带 __restart__ 标记）
    2. 在独立进程中启动 _restart_agent.py（脱离当前进程树）
    3. 当前会话收到 stream_end，前端断开 WebSocket
    4. _restart_agent.py 杀掉 8007 端口进程并重启 main.py
    """
    flag = early_stop.get("flag", "")

    if flag == "__restart__":
        WORK = str(Path(__file__).resolve().parent.parent)
        SCRIPT = Path(WORK) / "shared" / "tool" / "_restart_agent.py"

        log_file = early_stop.get("log_file", "")
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_file, "ab", buffering=0)
        else:
            log_fh = subprocess.DEVNULL

        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(SCRIPT), session_id,
            cwd=WORK, stdout=log_fh, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        if log_file:
            log_fh.write(f"PID: {proc.pid}\n".encode())
            log_fh.close()

    q.put_nowait({"type": "stream_end", "final_text": ""})
