import shlex
import sys
from datetime import datetime
from typing import Annotated

import asyncio

from pydantic import Field
from ai_agent.utils import tool, safe_decode, kill_proc, env
from ai_agent.settings import settings
from ai_agent.utils import get_path
from pathlib import Path
import uuid


# --- 智能兜底：python -c 代码直接落盘执行，砍掉命令行参数传递 ---

def _needs_temp_script(cmd):
    """检测 python -c 或 python /c 是否应转为临时脚本执行。

    兼容两种平台：Windows 上 AI 可能误用 /c（cmd.exe 风格），
    macOS/Linux 上 AI 用 -c（Python 标准）。无论哪种，代码落盘执行更安全。"""
    if len(cmd) < 3:
        return False
    exe = Path(cmd[0]).name
    if not exe.startswith('python'):
        return False
    if cmd[1] not in ('/c', '-c'):
        return False
    return True  # 无条件兜底，全部走临时脚本


@tool(r"""执行非交互式命令。默认前台（等待完成返回输出）。
Windows 上 npm/npx/yarn 等直接传 ['npm.cmd','install'] 即可，不要手动套 cmd /c；
macOS/Linux 直接用 ['npm','install'] 即可。
参数 daemon 为 True 表示启动后台任务（如服务器），会立刻返回 pid 和日志文件路径（用于实时观察）。""")
async def subprocess_exec(
        cmd: Annotated[
            list[str], Field(description="命令数组")],
        cwd: Annotated[str, Field(description="工作目录")],
        timeout: Annotated[int, Field(description="超时秒")] = 60,
        daemon: Annotated[bool, Field(description="是否启动后台任务")] = False,
):
    tmp_file = None
    try:
        # python -c → 代码直接落盘，不经过命令行参数传递
        if _needs_temp_script(cmd):
            code = cmd[2]
            tmp_file = (Path(cwd) / f'_run_{uuid.uuid4().hex[:8]}.py').resolve()
            tmp_file.write_text(code, encoding='utf-8')
            cmd = [cmd[0], str(tmp_file)]
        if daemon:
            log_dir = Path(settings.work_dir).absolute() / ".daemon_logs"
            log_dir.mkdir(exist_ok=True)

            log_file = log_dir / f"{uuid.uuid4()}.log"

            # 二进制模式！
            log_fh = open(log_file, "ab", buffering=0)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=log_fh,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )

            log_fh.write(f"PID: {proc.pid}\n".encode())

            log_fh.close()

            return f"后台进程已启动\nPID={proc.pid}\n日志文件：{log_file}"
        # 前台模式
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            await kill_proc(proc.pid)
            return "⚠️ 命令超时，已终止"
        except asyncio.CancelledError:
            await kill_proc(proc.pid)
            raise  # 重新抛出，让上层取消流程继续

        out = safe_decode(stdout)
        err = safe_decode(stderr)

        if proc.returncode == 0:
            return out
        else:
            return f"(code={proc.returncode})\n{err or out}"

    except Exception as e:
        return f"❌ 执行异常: {e}"
    finally:
        if tmp_file:
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:
                pass


@tool(r"""执行python文件""")
async def python_exec(
        path: Annotated[
            str, Field(description="python文件绝对路径")],
        timeout: Annotated[int, Field(description="超时秒")] = 120,
):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        await kill_proc(proc.pid)
        return "⚠️ 命令超时，已终止"
    except asyncio.CancelledError:
        await kill_proc(proc.pid)
        raise  # 重新抛出，让上层取消流程继续
    out = safe_decode(stdout)
    err = safe_decode(stderr)
    if proc.returncode == 0:
        return f"\n\n{out}"
    else:
        return f"(code={proc.returncode})\n\n{err or out}"


@tool("查看日志文件最后N行")
def tail_log(
        path: Annotated[str, Field(description="日志路径")],
        n: Annotated[int, Field(description="行数")] = 100,
):
    p = get_path(path)
    if not p.exists():
        return "文件不存在"

    raw = p.read_bytes()
    text = safe_decode(raw)
    lines = text.splitlines()
    return "\n".join(lines[-n:])


@tool("通过PID清理后台进程")
async def kill_daemon(
        pid: Annotated[
            list[int], Field(description="进程PID（由 run_cmd(daemon=True) 返回）")],
):
    for p in pid:
        await kill_proc(p)
    return "成功"


@tool("让 AI 主动等待指定秒数后再继续。")
async def wait(
        seconds: Annotated[float, Field(description="等待秒数，支持小数")] = 5,
):
    await asyncio.sleep(seconds)
    return f"✓ 已等待 {seconds} 秒。"
