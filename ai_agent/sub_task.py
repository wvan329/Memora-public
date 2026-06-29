"""
子 AI 委托编排器 —— 统一批量执行。

每层子 AI 创建自己的 asyncio.Queue，覆盖 parent_queue ContextVar 实现视图隔离：
- delegate_batch_start 推给父队列 → 父前端弹出多页滑动弹窗（n=1 时单页）
- 子 AI 的 text/reason chunk 通过双推引擎转发给父队列
- client_action_request 推给自己队列 → 自己前端响应
- browser_inner 角色：浏览器生命周期由 BrowserSessionManager 统一管理

ContextVar 视图隔离原理：
每个 asyncio.Task 在创建时会复制父任务的 ContextVar 上下文（copy-on-write）。
因此 _run_one 内部通过 parent_queue_ctx.set(my_queue) 覆盖后，
只有当前 Task 及其子协程看到 my_queue，其他并行 Task 互不干扰。
这保证了子 AI 之间的输出隔离，同时父队列仍能收到双推的 chunk。

v4.3：修复 CancelledError 捕获 —— Python 3.9+ 中 CancelledError 继承 BaseException
而非 Exception，except Exception 捕获不到。改为 except BaseException 确保
consumer_task 在 abort 时被正确取消。
"""
import asyncio
import contextvars
import json as _json
import logging
import uuid

_log = logging.getLogger(__name__)

from ai_agent.conversation import ConversationTurn
from ai_agent.permissions import current_tool_call_id, browser_session_id
from ai_agent.browser_session_manager import get_browser_session_manager


class SubTaskRunner:
    """子 AI 委托编排器。

    依赖（全部通过构造器注入）：
        session_mgr: 用于创建消费者消费子 AI 的输出队列
        repo:        标记子会话（不出现在历史列表）
        tool_registry: 未直接使用（传给 ConversationTurn）
        client_action: 未直接使用（传给 ConversationTurn）
        parent_queue_ctx: 父队列 ContextVar，用于推送 delegate_batch_start + 视图隔离
    """

    def __init__(self, session_mgr, repo, tool_registry, client_action,
                 parent_queue_ctx: contextvars.ContextVar):
        self.session_mgr = session_mgr
        self.repo = repo
        self.tool_registry = tool_registry
        self.client_action = client_action
        self.parent_queue_ctx = parent_queue_ctx

    async def run_batch(self, items: list[dict], role: str) -> dict:
        """并行执行多个子任务。

        Args:
            items: [{"task": str, "session_uuid": str}, ...]
                   session_uuid 为空 → 自动新建；已有 → 复用历史上下文
            role:  子 AI 角色名（delegate / browser_inner / compress）

        Returns:
            {"success": bool, "sessions": [{"session_uuid": str, "success": bool, "result": str}, ...]}
        """
        tc_id = current_tool_call_id.get()
        parent_q = self.parent_queue_ctx.get()

        # 拆出任务描述和 session_uuid
        tasks = [it["task"] for it in items]
        sids = [
            it.get("session_uuid", "").strip() or str(uuid.uuid4())
            for it in items
        ]

        # 标记所有子会话
        for sid in sids:
            await self.repo.mark_sub_session(sid)

        # 原子加锁
        for sid in sids:
            if not self.session_mgr.try_claim(sid):
                for prev in sids:
                    if prev == sid:
                        break
                    self.session_mgr.release(prev)
                dup_sid = sid
                return {
                    "success": False,
                    "sessions": [],
                    "error": (
                        f"session_uuid 冲突：'{dup_sid}' 被多个并行任务同时使用。"
                        "并行任务各自有独立的会话上下文，不能共享同一个 session_uuid。"
                        "解决：① 让每个任务使用不同的 session_uuid（空字符串=自动新建）；"
                        "② 如果需要复用历史上下文，请拆成多次单独调用（n=1），每次传一个任务，或者把多个任务一次性说完。"
                    ),
                }

        # 通知父前端
        if parent_q:
            await parent_q.put({
                "type": "delegate_batch_start",
                "tool_call_id": tc_id,
                "sessions": [
                    {"session_uuid": sid, "index": i, "task": task}
                    for i, (sid, task) in enumerate(zip(sids, tasks))
                ],
            })

        # 并行执行。每个子任务包装为独立 asyncio.Task。
        # v4.5：将子 task 注册到 SessionManager，使前端能直接 cancel 浏览器子 AI。
        sub_tasks: list[asyncio.Task] = []
        async def _run_one(sid: str, task: str, cleanup: bool = True) -> dict:
            my_queue: asyncio.Queue = asyncio.Queue()
            # 覆盖 parent_queue ContextVar，实现视图隔离。
            # 为什么安全？asyncio.Task 创建时会 copy-on-write 复制 ContextVar 上下文，
            # 此处 set 只影响当前 Task 及其子协程，其他并行 Task 不受影响。
            self.parent_queue_ctx.set(my_queue)

            if role == "browser_inner":
                browser_session_id.set(sid)
                if not cleanup:
                    get_browser_session_manager().mark_busy(sid)

            consumer_task = asyncio.create_task(
                self.session_mgr.consume_queue(my_queue, sid,
                                               parent_q=parent_q, tc_id=tc_id)
            )

            # 标记是否被中断：中断 = 用户明确不需要该浏览器 → 强制清理
            _aborted = False
            try:
                turn = ConversationTurn(
                    session_id=sid, prompt=task, role=role,
                    repo=self.repo, tool_registry=self.tool_registry,
                    session_mgr=self.session_mgr,
                )
                await turn.execute()
                result_text = await consumer_task
                return {"session_uuid": sid, "success": True, "result": result_text}
            except BaseException as _exc:
                # ★ v4.3：用 BaseException 替代 Exception。
                # Python 3.9+ 中 asyncio.CancelledError 继承 BaseException，
                # except Exception 捕获不到 → consumer_task 成为孤儿。
                _log.error(f"子任务 {sid} 异常: {type(_exc).__name__}: {_exc!r}")
                _aborted = True
                if not consumer_task.done():
                    consumer_task.cancel()
                raise
            finally:
                self.session_mgr.release(sid)
                if role == "browser_inner":
                    # 被中断时忽略 cleanup 参数，强制清理浏览器。
                    # 用户主动中断 = 明确不需要该浏览器实例了。
                    if _aborted or cleanup:
                        from shared.tool.playwright import _force_cleanup_browser
                        # asyncio.shield 防止 finally 中的清理被 cancel 打断
                        try:
                            await asyncio.shield(_force_cleanup_browser(sid))
                        except BaseException as e:
                            _log.warning(f"强制清理浏览器 {sid} 失败: {e}")
                    else:
                        get_browser_session_manager().mark_idle(sid)

        for sid, task, it in zip(sids, tasks, items):
            t = asyncio.create_task(
                _run_one(sid, task, it.get("cleanup", True)))
            sub_tasks.append(t)
            # 注册子 task：使前端 abort 浏览器子 AI 时能真正 cancel
            # （此前只 try_claim 占坑 None，get_task 返回 None → 中断无效）
            self.session_mgr.set_task(sid, t)

        raw_results = await asyncio.gather(*sub_tasks, return_exceptions=True)
        sessions: list[dict] = []
        for r in raw_results:
            if isinstance(r, BaseException):
                _log.error(f"子任务执行失败: {type(r).__name__}: {r!r}")
                sessions.append({"success": False, "error": str(r)})
            else:
                sessions.append(r)
        return {
            "success": all(s.get("success", False) for s in sessions),
            "sessions": sessions,
        }
