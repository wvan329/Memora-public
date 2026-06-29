# tools/git_pull.py
"""
Git 拉取工具 — 从远程仓库拉取最新代码
"""
import asyncio
from typing import Annotated

from pydantic import Field

from ai_agent.utils import tool
from ai_agent.settings import ROOT


@tool("""从远程仓库拉取最新代码（git pull）。

当用户说「拉取代码」「git pull」「pull」「更新代码」时调用，无需确认。
支持指定远程分支名，默认拉取当前跟踪分支。""")
async def git_pull(
    branch: Annotated[str | None, Field(description="要拉取的远程分支名，如 'main'、'origin master'。不填则拉取当前跟踪分支")] = None,
) -> str:
    cwd = str(ROOT)
    try:
        cmd = ["git", "pull"]
        if branch:
            cmd.append(branch)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return f"❌ git pull 失败:\n{output}"

        if output.strip():
            return f"✅ 拉取完成:\n{output.strip()}"
        else:
            return "✅ 已是最新，无需拉取。"

    except Exception as e:
        return f"❌ 执行异常: {e}"
