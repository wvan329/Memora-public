"""
浏览器会话生命周期管理器 —— 后台定时扫描 + TTL 回收。

职责：
- 跟踪所有活跃浏览器会话（busy/idle 状态）
- 后台定时扫描 Playwright daemon 目录，自动发现未知会话
- 空闲超时（TTL_IDLE）自动清理
- 模块级单例，通过 get_browser_session_manager() 获取
"""
import asyncio
import logging
import time

_log = logging.getLogger(__name__)

# 延迟导入以断开循环依赖：
#   tools.playwright → tools.shell_tool → ai_agent.utils → ai_agent.container
#   → ai_agent.sub_task → ai_agent.browser_session_manager
# 在 _cleanup_loop / stop 内部按需 import。


# ── 模块级单例 ──────────────────────────────────────────────
_instance: "BrowserSessionManager | None" = None


def get_browser_session_manager() -> "BrowserSessionManager":
    """获取 BrowserSessionManager 模块级单例。"""
    global _instance
    if _instance is None:
        _instance = BrowserSessionManager()
    return _instance


class BrowserSessionManager:
    """浏览器会话管理器：跟踪所有 Playwright 浏览器会话，按 TTL 自动回收。

    生命周期：
        manager = get_browser_session_manager()
        await manager.start()   # 启动后台清理循环
        ...
        await manager.stop()    # 停止循环并清理所有会话
    """

    # 空闲超时：会话标记为 idle 后 10 分钟无活动即清理
    TTL_IDLE: float = 600

    # 后台扫描间隔：每 60 秒扫描一次 daemon 目录
    SCAN_INTERVAL: float = 60

    def __init__(self) -> None:
        """初始化会话字典和后台任务引用。"""
        # key: session_uuid  value: {"busy": bool, "idle_since": float}
        self._sessions: dict[str, dict] = {}
        self._task: asyncio.Task | None = None

    # ── 状态标记 ────────────────────────────────────────────

    def mark_busy(self, sid: str) -> None:
        """标记会话为忙碌状态，清理循环会跳过。"""
        self._sessions[sid] = {"busy": True, "idle_since": 0}

    def mark_idle(self, sid: str) -> None:
        """标记会话为空闲状态，开始 TTL 倒计时。"""
        self._sessions[sid] = {"busy": False, "idle_since": time.time()}

    # ── 后台清理循环 ────────────────────────────────────────

    async def _cleanup_loop(self) -> None:
        """后台循环：定时扫描 daemon 目录，清理空闲超时会话。

        流程：
        1. 每 SCAN_INTERVAL 秒执行一次
        2. 从文件系统发现未知 .session 文件并注册（跳过 default.session）
        3. busy 会话跳过；idle 超 TTL_IDLE 则清理
        """
        from shared.tool.playwright import _get_daemon_dir, _force_cleanup_browser

        while True:
            await asyncio.sleep(self.SCAN_INTERVAL)
            try:
                daemon_dir = _get_daemon_dir()
                if daemon_dir is None:
                    continue

                now = time.time()

                # ── 从文件系统发现未知会话（重启后恢复）──
                for session_file in daemon_dir.glob("*.session"):
                    if session_file.stem == "default":
                        continue
                    sid = session_file.stem
                    if sid not in self._sessions:
                        self._sessions[sid] = {
                            "busy": False,
                            "idle_since": session_file.stat().st_mtime,
                        }

                # ── 清理空闲超时会话 ──
                to_remove: list[str] = []
                for sid, info in list(self._sessions.items()):
                    if info["busy"]:
                        continue
                    idle_since = info["idle_since"]
                    if idle_since and (now - idle_since) > self.TTL_IDLE:
                        try:
                            await _force_cleanup_browser(sid)
                        except Exception:
                            pass
                        to_remove.append(sid)

                for sid in to_remove:
                    del self._sessions[sid]

            except asyncio.CancelledError:
                raise
            except Exception as e:
                _log.warning(f"cleanup 循环异常: {e}")

    # ── 生命周期 ────────────────────────────────────────────

    async def start(self) -> None:
        """启动后台清理循环任务。幂等：重复调用不会创建多个任务。"""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """停止后台清理循环，并清理所有已注册的浏览器会话。幂等。"""
        from shared.tool.playwright import _force_cleanup_browser

        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        for sid in list(self._sessions.keys()):
            try:
                await _force_cleanup_browser(sid)
            except Exception as e:
                _log.warning(f"stop 清理浏览器 {sid} 失败: {e}")

        self._sessions.clear()
