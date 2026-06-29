"""
frpc 健康守护 — 每 2 分钟检测一次，异常重连自动重启。
仅 Windows 生效（Mac 端 frpc 稳定，无需此机制）。

ssh_exec 延迟导入，避免模块加载时 AppContainer 尚未初始化的循环依赖。
"""
import asyncio
import subprocess

from pathlib import Path

_FRPC_START = str(Path(__file__).resolve().parent.parent / "启动frpc.vbs")


async def _restart_frpc():
    subprocess.run(["taskkill", "/F", "/IM", "frpc.exe"], capture_output=True)
    await asyncio.sleep(2)
    subprocess.Popen(["wscript", _FRPC_START])


async def frpc_watchdog():
    """每 2 分钟通过 frps 日志检测 kwg 是否异常重连，发现即重启。"""
    from shared.tool.ssh_tool import ssh_exec

    await asyncio.sleep(30)  # 等容器初始化完毕

    while True:
        try:
            result = await ssh_exec(
                command="docker logs frps --since 2m 2>&1 | grep -c 'hostname.*kwg'",
                timeout=10,
            )
            count = int(result.strip() or "0")
            if count > 2:
                await _restart_frpc()
        except Exception:
            pass
        await asyncio.sleep(120)
