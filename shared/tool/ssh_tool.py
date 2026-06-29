# tools/ssh_tool.py
"""
SSH 远程执行工具 — 通过 asyncssh 异步连接远程服务器执行命令、上传/下载文件。
凭据从 .env 读取（SSH_HOST / SSH_PORT / SSH_USER / SSH_PASSWORD）。
"""
import os
import asyncio
from typing import Annotated
from pydantic import Field
from ai_agent.utils import tool
from ai_agent.settings import settings


@tool("""SSH 远程执行命令 / 上传下载文件 — 在阿里云 ECS 服务器上执行 shell 命令，或通过 SFTP 上传/下载文件。command、upload、download 至少传一个，都传则先上传、再下载、最后执行命令。""")
async def ssh_exec(
    command: Annotated[str | None, Field(description="要执行的 shell 命令")] = None,
    timeout: Annotated[int, Field(description="命令超时秒数，默认 30")] = 30,
    upload: Annotated[str | None, Field(description="本地文件绝对路径，SFTP 上传到服务器。需同时传 remote_path")] = None,
    remote_path: Annotated[str | None, Field(description="远程目标绝对路径，upload 时必填，如 '/root/project/index.html'")] = None,
    download: Annotated[str | None, Field(description="远程文件绝对路径，SFTP 下载到本地。需同时传 local_path")] = None,
    local_path: Annotated[str | None, Field(description="本地目标绝对路径，download 时必填，如 'D:/work/file.txt'")] = None,
) -> str:
    try:
        import asyncssh

        if not command and not upload and not download:
            return "❌ 请至少传 command、upload 或 download 参数之一"

        _host = settings.ssh_host
        _port = settings.ssh_port
        _user = settings.ssh_user
        _pwd = settings.ssh_password

        if not _host:
            return "❌ 未配置 SSH_HOST，请在 .env 中设置"

        async with asyncssh.connect(
            _host, port=_port, username=_user, password=_pwd,
            known_hosts=None, connect_timeout=15,
        ) as conn:
            result_parts = []

            # 1. SFTP 上传
            if upload:
                if not remote_path:
                    return "❌ upload 需要同时传 remote_path 参数"
                if not os.path.isfile(upload):
                    return f"❌ 本地文件不存在: {upload}"

                async with conn.start_sftp_client() as sftp:
                    await sftp.put(upload, remote_path)
                size = os.path.getsize(upload)
                result_parts.append(f"✅ 已上传 {size} bytes → {remote_path}")

            # 2. SFTP 下载
            if download:
                if not local_path:
                    return "❌ download 需要同时传 local_path 参数"

                os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
                async with conn.start_sftp_client() as sftp:
                    await sftp.get(download, local_path)
                size = os.path.getsize(local_path)
                result_parts.append(f"✅ 已下载 {size} bytes ← {download}")

            # 3. 执行命令
            if command:
                result = await conn.run(command, timeout=timeout)
                out = result.stdout.strip() if result.stdout else ""
                err = result.stderr.strip() if result.stderr else ""

                if out:
                    result_parts.append(out)
                if err:
                    result_parts.append(f"[stderr]\n{err}")

            if not result_parts:
                return "（命令无输出）"
            return "\n".join(result_parts)

    except asyncio.TimeoutError:
        return f"❌ SSH 执行超时（{timeout}s），远端命令可能仍在运行"
    except Exception as e:
        return f"❌ SSH 执行失败 [{type(e).__name__}]: {e}"
