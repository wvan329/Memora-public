"""
消息路由器 —— 替代旧 ws_endpoint.py 中的 if-else 分发。

每种 WebSocket 消息类型注册一个 async handler，
MessageRouter 只负责查找 handler 并调用，不关心 handler 内部逻辑。

handler 签名: async (ws, msg, subscribed_sid) -> new_subscribed_sid

v4.3：_on_abort 改进 —— cancel task 后等待最多 3 秒，
让 ConversationTurn._complete_aborted_tools 有机会补入库。
"""
import asyncio
import json
import logging
from typing import Callable, Awaitable

from fastapi import WebSocket, WebSocketDisconnect

from ai_agent.settings import settings

# handler 类型别名
Handler = Callable[[WebSocket, dict, str | None], Awaitable[str | None]]


class MessageRouter:
    """WebSocket 消息路由器。

    依赖（通过构造器注入）：
        session_mgr:          会话管理（连接池、广播、队列消费、运行任务）
        client_action:        客户端动作请求/响应
        repo:                 消息持久化（subscribe 时加载历史）
        mobile_pool:          手机连接池（global 端点用）
        conversation_factory: 工厂函数 (sid, prompt, role) → ConversationTurn

    扩展方式：新增消息类型只需实现 Handler 并 register()，不动现有代码。
    """

    def __init__(self, session_mgr, client_action, repo, mobile_pool,
                 conversation_factory):
        self.session_mgr = session_mgr
        self.client_action = client_action
        self.repo = repo
        self.mobile_pool = mobile_pool
        self._conversation_factory = conversation_factory

        # 消息类型 → handler 注册表
        self._handlers: dict[str, Handler] = {}
        self._register_defaults()

    def register(self, msg_type: str, handler: Handler) -> None:
        """注册消息类型对应的 handler。

        Args:
            msg_type: WebSocket 消息的 type 字段值（如 "subscribe"）
            handler:  处理函数，签名: async (ws, msg, subscribed_sid) -> new_subscribed_sid
        """
        self._handlers[msg_type] = handler

    # ═══════════════════════════════════════════════════════════════
    # 端点入口（由 main.py 的 ws_endpoint 调用）
    # ═══════════════════════════════════════════════════════════════

    async def handle_connection(self, ws: WebSocket, conn_type: str) -> None:
        """分流入口。

        conn_type == "global" → 手机全局连接（剪贴板 + 通知）
        其他                 → 浏览器聊天连接（subscribe/chat/abort）
        """
        if conn_type == "global":
            await self._handle_global(ws)
        else:
            await self._handle_chat(ws)

    # ── 全局连接（手机）──

    async def _handle_global(self, ws: WebSocket) -> None:
        """处理手机全局 WebSocket 连接。

        消息类型：
            clipboard_push  → 手机剪贴板变更 → 写入电脑剪贴板
            request_clipboard → 手机请求拉取电脑剪贴板
        """
        await ws.accept()
        self.mobile_pool.register(ws, path=str(ws.url.path))
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                if msg.get("type") == "clipboard_push":
                    text = msg.get("content", "")
                    if self.session_mgr.clipboard_monitor:
                        self.session_mgr.clipboard_monitor.set_clipboard(text)
                elif msg.get("type") == "request_clipboard":
                    import pyperclip
                    try:
                        text = pyperclip.paste() or ""
                    except Exception:
                        text = ""
                    if not text:
                        text = "[PC 剪贴板为空]"
                    await ws.send_json({"type": "clipboard_sync", "content": text})
        except WebSocketDisconnect:
            pass
        finally:
            self.mobile_pool.unregister(ws)

    # ── 聊天连接（浏览器）──

    async def _handle_chat(self, ws: WebSocket) -> None:
        """处理浏览器聊天 WebSocket 连接。

        消息循环：读取 JSON → 按 type 查找 handler → 调用。
        subscribed_sid 在 handler 之间传递，跟踪当前订阅的会话。
        """
        await ws.accept()
        subscribed_sid: str | None = None
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                mtype = msg.get("type", "")
                handler = self._handlers.get(mtype)
                if handler:
                    subscribed_sid = await handler(ws, msg, subscribed_sid)
        except WebSocketDisconnect:
            pass
        finally:
            if subscribed_sid:
                await self.session_mgr.unsubscribe(subscribed_sid, ws)

    # ═══════════════════════════════════════════════════════════════
    # 默认 handler
    # ═══════════════════════════════════════════════════════════════

    def _register_defaults(self) -> None:
        """注册内置消息类型的 handler。"""
        self.register("subscribe", self._on_subscribe)
        self.register("chat", self._on_chat)
        self.register("abort", self._on_abort)
        self.register("discard_buffer", self._on_discard_buffer)
        self.register("client_action_result", self._on_client_action_result)

    async def _on_subscribe(self, ws: WebSocket, msg: dict, sub_sid: str | None) -> str | None:
        """subscribe handler：推送历史消息 + 实时 chunk 快照 + 缓冲区状态。

        使用 asyncio.Lock 保证与 chunk 写入互斥：
        先拿锁 → 加载历史 → 推送快照 → 注册订阅 → 释放锁。
        这样不会有 chunk 在快照之后、订阅之前丢失。
        """
        sid = msg.get("session_id", "")
        lock = self.session_mgr.get_or_create_lock(sid)
        async with lock:
            messages = await self.repo.load(sid)
            # 告知前端该会话是否有正在运行的 AI 任务，
            # 前端据此决定是否复用最后一条 AI 消息（避免刷新后出现两个 💭）。
            has_task = self.session_mgr.has_task(sid)
            await ws.send_json({"type": "history", "messages": messages, "streaming": has_task})
            if self.session_mgr.has_chunks(sid):
                for chunk in self.session_mgr.get_chunks(sid):
                    await ws.send_json(chunk)
            # 推送缓冲区状态：前端刷新后可恢复待发送指示器
            buf = self.session_mgr.get_buffer(sid)
            if buf:
                await ws.send_json({"type": "buffer_status", "content": buf})
            return await self.session_mgr.switch_subscription(sub_sid, sid, ws)

    async def _on_chat(self, ws: WebSocket, msg: dict, sub_sid: str | None) -> str | None:
        """chat handler：用户发送消息。

        流程：
            1. 切换订阅到目标会话
            2. try_claim：
               - 成功 → 创建 task 启动对话循环（run_turn 完成后自动消费缓冲区）
               - 失败 → 后端忙，消息写入缓冲区（大小为一，自动覆盖旧值）
        """
        sid = msg.get("session_id", "")
        prompt = msg.get("prompt", "").strip()
        new_sid = await self.session_mgr.switch_subscription(sub_sid, sid, ws)

        if not self.session_mgr.try_claim(sid):
            # 后端忙 → 写入缓冲区（覆盖旧值），不返回 busy
            self.session_mgr.set_buffer(sid, prompt)
            return new_sid

        from ai_agent.permissions import parent_queue

        # 通知回调：一轮对话彻底完成后推送手机通知。
        # 定义在此处而非 SessionManager 内部，因为 SessionManager 不应
        # 知道"通知"的存在——它只负责会话生命周期管理。
        async def _notify_complete(_sid: str, last_reply: str):
            from shared.tool.toggle_notification import is_notification_enabled
            if is_notification_enabled():
                content = last_reply[:200] + ("..." if len(last_reply) > 200 else "")
                await self.mobile_pool.broadcast({
                    "type": "push_notification",
                    "title": "任务完成",
                    "content": content,
                    "session_id": _sid,
                })

        task = asyncio.create_task(
            self.session_mgr.run_turn(sid, prompt, "main",
                                      self._conversation_factory, parent_queue,
                                      on_complete=_notify_complete)
        )
        self.session_mgr.set_task(sid, task)
        return new_sid

    async def _on_abort(self, ws: WebSocket, msg: dict, sub_sid: str | None) -> str | None:
        """abort handler：取消当前会话的 AI 任务，清空缓冲区。

        v4.3：cancel task 后等待最多 3 秒宽限期，
        让 ConversationTurn._complete_aborted_tools 有机会补入库。

        v5.0：超时时标记为 stuck 而非静默 pass。
        stuck 标记保留 try_claim 占坑（防并发），has_task 返回 False（前端不假显示）。
        旧 task 恢复后走 CancelledError 自然 release。
        """
        sid = msg.get("session_id", "")
        self.session_mgr.clear_buffer(sid)
        task = self.session_mgr.get_task(sid)
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                # task 卡死（AI API hang），标记为 stuck。
                # try_claim 仍拒绝新任务，has_task 返回 False。
                # 旧 task 恢复后走 CancelledError → release(sid) 自动清理。
                _logger = logging.getLogger(__name__)
                _logger.warning(
                    f"abort 超时：会话 {sid} 的 AI 任务 3 秒内未响应，标记为 stuck。"
                    f"可能原因：AI API 请求 hang 住（TCP 半开/防火墙黑洞）。"
                )
                self.session_mgr.mark_stuck(sid)
            except asyncio.CancelledError:
                pass
        await ws.send_json({"type": "aborted"})
        return sub_sid

    async def _on_discard_buffer(self, ws: WebSocket, msg: dict, sub_sid: str | None) -> str | None:
        """discard_buffer handler：仅清空缓冲区（不取消 AI 任务）。"""
        sid = msg.get("session_id", "")
        self.session_mgr.clear_buffer(sid)
        return sub_sid

    async def _on_client_action_result(self, ws: WebSocket, msg: dict,
                                       sub_sid: str | None) -> str | None:
        """client_action_result handler：前端发回客户端动作结果。

        1. 调用 ClientActionManager.resolve 唤醒等待的 Future
        2. 广播 client_action_resolved 通知所有窗口关闭弹窗
        """
        rid = msg.get("request_id", "")
        self.client_action.resolve(rid, msg.get("result", {}))
        # 广播弹窗已关闭，通知同一 session 的其他窗口自动关闭弹窗
        if sub_sid:
            await self.session_mgr.broadcast(sub_sid, {
                "type": "client_action_resolved",
                "request_id": rid,
                "tool_call_id": msg.get("tool_call_id", ""),
            })
        return sub_sid
