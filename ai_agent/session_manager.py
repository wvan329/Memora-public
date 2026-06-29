"""
会话管理器 —— 封装 WebSocket 连接池、chunk 缓存、锁、运行任务。

替代旧 ws_manager.py 的 5 个模块级全局变量：
    _ws_connections → self._connections
    _session_chunks  → self._chunks
    _session_locks   → self._locks
    _running_tasks   → self._tasks
    _clipboard_monitor → self.clipboard_monitor

所有公开方法都是 async，确保 asyncio 锁安全。
"""
import asyncio
from typing import Optional, Any
from fastapi import WebSocket


class SessionManager:
    """会话生命周期管理：连接订阅、广播、队列消费、运行任务。

    由 AppContainer 创建单例，注入到 MessageRouter 和 ConversationTurn。
    clipboard_monitor 由 main.py lifespan 在容器初始化后注入。
    """

    def __init__(self):
        # {session_id: [WebSocket, ...]} —— 同一会话可能被多个设备/标签页订阅
        self._connections: dict[str, list[WebSocket]] = {}
        # {session_id: [chunk, ...]} —— 未入库的实时流式 chunk（subscribe 时快照推送）
        self._chunks: dict[str, list[dict]] = {}
        # {session_id: asyncio.Lock} —— 保证 subscribe 与 chunk 写入互斥
        self._locks: dict[str, asyncio.Lock] = {}
        # {session_id: asyncio.Task} —— 运行中的 AI 任务，防重入 + abort
        self._tasks: dict[str, asyncio.Task] = {}
        # {session_id: str} —— 大小为一的缓冲区：后端忙时暂存用户新消息，run_turn 完成后自动消费
        self._buffer: dict[str, str] = {}
        # NativeClipboardListener 实例，由 main.py 注入。
        # SessionManager 不负责创建，只持有引用供 WS 端点读取。
        self.clipboard_monitor: Optional[Any] = None

    # ═══════════════════════════════════════════════════════════════
    # 连接管理
    # ═══════════════════════════════════════════════════════════════

    async def subscribe(self, sid: str, ws: WebSocket) -> None:
        """将 WebSocket 加入指定会话的订阅列表。"""
        self._connections.setdefault(sid, []).append(ws)

    async def unsubscribe(self, sid: str, ws: WebSocket) -> None:
        """将 WebSocket 从指定会话退订。

        当会话的连接数归零时，同步释放锁（chunk 缓存保留，供下次 subscribe 快照）。
        注意：不在此处清理 _tasks，因为任务可能仍在运行。
        """
        if sid not in self._connections:
            return
        try:
            self._connections[sid].remove(ws)
        except ValueError:
            pass
        if not self._connections[sid]:
            del self._connections[sid]
            self._locks.pop(sid, None)

    async def switch_subscription(self, old_sid: str | None, new_sid: str,
                                  ws: WebSocket) -> str:
        """切换订阅的会话。

        从旧会话自动退订，加入新会话。如果新旧相同则无操作。
        返回新的 session_id。
        """
        if old_sid and old_sid != new_sid:
            await self.unsubscribe(old_sid, ws)
        if old_sid != new_sid:
            await self.subscribe(new_sid, ws)
        return new_sid

    # ═══════════════════════════════════════════════════════════════
    # 广播
    # ═══════════════════════════════════════════════════════════════

    async def broadcast(self, sid: str, chunk: dict) -> None:
        """向某会话的所有订阅者广播一条 JSON 消息。

        自动检测并清理断线连接（send_json 抛异常 → 退订）。
        """
        if sid not in self._connections:
            return
        dead = []
        for ws in list(self._connections[sid]):  # 快照迭代，避免 await 期间列表被并发修改
            try:
                await ws.send_json(chunk)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unsubscribe(sid, ws)

    async def cache_and_broadcast(self, sid: str, chunk: dict) -> None:
        """缓存 chunk 并广播。

        chunk 追加到 _chunks[sid]，供后续 subscribe 时快照推送。
        """
        self._chunks.setdefault(sid, []).append(chunk)
        await self.broadcast(sid, chunk)

    # ═══════════════════════════════════════════════════════════════
    # 队列消费
    # ═══════════════════════════════════════════════════════════════

    async def consume_queue(self, queue: asyncio.Queue, sid: str, *,
                            parent_q: asyncio.Queue | None = None,
                            tc_id: str = "") -> str:
        """消费 asyncio.Queue 中的 chunk，逐条缓存+广播。

        这是所有 AI（主/子/browser/compress）的统一消费者。
        通过 asyncio.Lock 保证与 subscribe 的 history+快照推送互斥。

        Args:
            queue:    AI 输出队列（由 ConversationTurn 写入）
            sid:      会话 ID
            parent_q: 父队列引用。非空时，text/reason chunk 双推给父前端。
            tc_id:    工具调用 ID。传递给双推 chunk 以关联到父前端的工具卡片。

        Returns:
            stream_end chunk 中 pop 出的 final_text。
        """
        lock = self._locks.setdefault(sid, asyncio.Lock())
        result_text = ""
        while True:
            chunk = await queue.get()
            async with lock:
                if not isinstance(chunk, dict):
                    continue
                ctype = chunk.get("type", "")
                if ctype == "stream_end":
                    # 流结束：pop final_text，缓存+广播 stream_end，break
                    result_text = chunk.pop("final_text", "")
                    await self.cache_and_broadcast(sid, chunk)
                    # 双推子任务完成通知：子 AI 各自完成时实时通知父前端，
                    # 父前端弹窗逐个子任务追加 ✅ 完成标记，不再等 asyncio.gather 全部完成。
                    if parent_q and tc_id:
                        parent_q.put_nowait({
                            "type": "sub_task_end",
                            "tool_call_id": tc_id,
                            "session_uuid": sid,
                        })
                    break
                if chunk.get("_ephemeral"):
                    # 临时消息（delegate_batch_start、client_action_request 等）：
                    # 只广播不缓存，回放时不重复出现
                    await self.broadcast(sid, chunk)
                elif ctype == "__flush_cache__":
                    # 清空缓存（工具调用完成后，避免 chunk 重复）
                    self._chunks[sid] = []
                else:
                    await self.cache_and_broadcast(sid, chunk)
                # 双推：子 AI 的 text/reason/vision_chunk 实时转发给父前端
                if parent_q and ctype in ("text", "reason", "vision_chunk"):
                    parent_q.put_nowait({
                        **chunk,
                        "tool_call_id": tc_id,
                        "session_uuid": sid,
                    })
        return result_text

    # ═══════════════════════════════════════════════════════════════
    # 缓冲区（大小为一）：后端忙时暂存用户新消息，run_turn 完成后自动消费
    # ═══════════════════════════════════════════════════════════════

    def set_buffer(self, sid: str, prompt: str) -> None:
        """写入/覆盖缓冲区的待消费消息（大小为一，新消息覆盖旧消息）。"""
        self._buffer[sid] = prompt

    def pop_buffer(self, sid: str) -> str | None:
        """读取并清空缓冲区，返回待消费的消息；无消息返回 None。"""
        return self._buffer.pop(sid, None)

    def clear_buffer(self, sid: str) -> None:
        """清空缓冲区（abort 时调用）。"""
        self._buffer.pop(sid, None)

    def get_buffer(self, sid: str) -> str | None:
        """只读获取缓冲区内容（subscribe 时推送前端恢复状态）。"""
        return self._buffer.get(sid)

    # ═══════════════════════════════════════════════════════════════
    # 运行任务管理（防重入 + abort）
    # ═══════════════════════════════════════════════════════════════

    def try_claim(self, sid: str) -> bool:
        """尝试原子占用会话。成功返回 True，已被占用返回 False。

        检查 + 占坑一步完成，不依赖"中间无 await"的隐含假设。
        拿到 True 后必须通过 release() 释放，中途可调用 set_task() 绑定真实任务引用。
        """
        if sid in self._tasks:
            return False
        self._tasks[sid] = None  # 先占坑，后续 set_task 替换为真实 task
        return True

    def release(self, sid: str) -> None:
        """释放会话占用（无论成功/失败/取消都必须调用）。"""
        self._tasks.pop(sid, None)

    def set_task(self, sid: str, task: asyncio.Task) -> None:
        """绑定真实 asyncio.Task 引用（try_claim 占坑后调用，供 abort 取消）。"""
        self._tasks[sid] = task

    def get_task(self, sid: str) -> Optional[asyncio.Task]:
        """获取运行中任务，供 abort 取消。"""
        return self._tasks.get(sid)

    def has_task(self, sid: str) -> bool:
        """检查会话是否有运行中（或等待中）的 AI 任务。
        
        try_claim 占坑后即返回 True，subscribe 可用此标记告知前端
        会话正在流式输出，避免刷新后 chunk 快照创建重复 AI 消息。
        """
        return sid in self._tasks

    async def run_turn(self, sid: str, prompt: str, role: str,
                       conversation_factory, parent_queue_ctx,
                       on_complete: callable = None):
        """执行对话循环：消费 prompt，完成后检查缓冲区继续消费。

        循环直到缓冲区为空或被取消。

        Args:
            sid:                  会话 ID
            prompt:               本轮用户输入
            role:                 角色名（"main" / "delegate" / …）
            conversation_factory: 工厂函数 (sid, prompt, role) → ConversationTurn
            parent_queue_ctx:     ContextVar，用于传递队列给 ConversationTurn
            on_complete:          可选回调 (sid, last_reply) → None。
                                  缓冲区彻底空后调用，SessionManager 不关心回调做什么。
        """
        current_prompt = prompt
        last_reply = ""

        while True:
            self.init_chunks(sid)
            queue: asyncio.Queue = asyncio.Queue()
            parent_queue_ctx.set(queue)

            consumer = asyncio.create_task(self.consume_queue(queue, sid))
            try:
                turn = conversation_factory(sid, current_prompt, role)
                await turn.execute()
                final_text = await consumer
            except asyncio.CancelledError:
                consumer.cancel()
                self.clear_buffer(sid)
                self.release(sid)
                return
            except Exception as e:
                consumer.cancel()
                await self.broadcast(sid, {"type": "error", "content": str(e)})
            else:
                last_reply = (final_text or "").strip()
            finally:
                self.clear_chunks(sid)

            next_prompt = self.pop_buffer(sid)
            if next_prompt is None:
                self.release(sid)
                break
            current_prompt = next_prompt

        # 缓冲区彻底空了 → 通知外部（回调由 MessageRouter 注入）
        if last_reply and on_complete:
            await on_complete(sid, last_reply)

    # ═══════════════════════════════════════════════════════════════
    # Chunks + Locks 访问器（供 MessageRouter subscribe 处理使用）
    # ═══════════════════════════════════════════════════════════════

    def init_chunks(self, sid: str) -> None:
        """初始化会话的 chunk 缓存为空列表。"""
        self._chunks[sid] = []

    def clear_chunks(self, sid: str) -> None:
        """清空会话的 chunk 缓存（任务结束后调用）。"""
        self._chunks.pop(sid, None)

    def has_chunks(self, sid: str) -> bool:
        """检查会话是否有缓存的实时 chunk。"""
        return sid in self._chunks

    def get_chunks(self, sid: str) -> list[dict]:
        """获取会话的 chunk 快照（返回副本）。"""
        return list(self._chunks.get(sid, []))

    def get_or_create_lock(self, sid: str) -> asyncio.Lock:
        """获取或创建会话锁（subscribe 时使用，保证 history+快照 原子推送）。"""
        return self._locks.setdefault(sid, asyncio.Lock())
