"""
AI Agent 重启工具 — 弹出确认框，用户确认后返回重启标记，由 run.py 检测后执行实际重启。
"""
import uuid
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ai_agent.utils import tool, current_session_id
from ai_agent.container import get_container

# 动态获取项目根目录（shared/tool/restart_tool.py → 项目根目录）
WORK = str(Path(__file__).resolve().parent.parent.parent)


@tool("重启Memora服务")
async def schedule_restart(
    # skip_confirm: Annotated[bool, Field(description="跳过确认弹窗，直接重启（默认 False，即需要用户确认）")] = False,
) -> dict:
    try:

        result = await get_container().client_action.request("confirm", {
            "message": "确定要重启 AI Agent 服务吗？重启期间服务会短暂中断（约 2-3 秒）。",
            "confirm_text": "确认重启",
            "cancel_text": "取消",
        }, timeout=60)

        confirmed = result.get("confirmed")
        cancelled = result.get("cancelled")
        if cancelled or not confirmed:
            return {"content": "❌ 现在不要重启，立即停止重启的尝试。"}

        # 预验证：以 PREFLIGHT 模式运行 main.py，确认代码能正常加载
        import asyncio, subprocess, sys, os as _os2
        env = {**_os2.environ, "MEMORA_PREFLIGHT": "1"}
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "main.py"],
            cwd=WORK, env=env,
            capture_output=True, encoding="utf-8", timeout=30,
        )
        if result.returncode != 0:
            return {
                "content": (
                    "❌ 重启被阻止：代码存在错误，预验证失败。旧服务保持运行。\n\n"
                    f"错误详情：\n```\n{(result.stderr + result.stdout)[-800:]}\n```\n\n"
                    "请修复代码后重新重启。"
                )
            }

        log_dir = Path(WORK) / ".daemon_logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"restart_{uuid.uuid4().hex[:8]}.log"

        return {
            "content": f"✓ 预验证通过，正在重启...\n日志文件：{log_file}",
            "__restart__": True,
            "log_file": str(log_file),
        }
    except Exception as e:
        return {"content": f"❌ 安排重启失败: {e}"}
