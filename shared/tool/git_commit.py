# tools/git_commit.py
"""
Git 提交工具 — stage + commit + push
"""
import asyncio
from typing import Annotated

from pydantic import Field

from ai_agent.utils import tool
from ai_agent.settings import settings, ROOT


@tool("""将当前所有修改提交到 GitHub 仓库并推送（commit message 固定为 'fix'）。

执行流程：git add -A → git commit -m 'fix' → git push。
当用户说「提交代码」「提交并推送」「commit」「push」时调用，无需确认。""")
async def git_commit() -> str:
    cwd = str(ROOT)
    try:
        # 1. git add -A
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"❌ git add 失败:\n{stderr.decode('utf-8', errors='replace')}"

        # 2. git commit
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", "fix",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        commit_output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            # 检查是否没有修改可提交
            if "nothing to commit" in commit_output.lower():
                return "⚠️ 没有需要提交的修改，工作区已是干净状态。"
            return f"❌ git commit 失败:\n{commit_output}"

        # 3. git push
        proc = await asyncio.create_subprocess_exec(
            "git", "push",
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        push_output = stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            return f"✅ 已提交，但推送失败:\n{push_output}"

        return f"✅ 已提交并推送:\n{commit_output.strip()}\n{push_output.strip()}"

    except Exception as e:
        return f"❌ 执行异常: {e}"
