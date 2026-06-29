"""
客户端动作管理器 —— 请求手机/浏览器执行动作并等待响应。

替代旧 utils.py 中的 _pending_actions 全局 dict + request_client_action 函数。

工作流程：
    1. AI 工具调用 request_client_action("get_location")
    2. 通过 parent_queue 推送 client_action_request 到前端
    3. 前端执行动作后通过 WebSocket 发回 client_action_result
    4. resolve() 唤醒等待的 asyncio.Future → 返回结果给 AI

关键约束：
    - parent_queue_ctx 由外部注入（AppContainer），避免循环导入
    - resolve() 幂等：重复 resolve 同一个 request_id 不会崩溃
"""
import asyncio
import contextvars
import uuid

from ai_agent.permissions import current_tool_call_id


class ClientActionManager:
    """请求客户端执行动作，等待异步响应。

    依赖（通过构造器注入）：
        parent_queue_ctx: parent_queue ContextVar，用于推送请求到前端。
                         注意：这是 ContextVar 对象本身，不是值。
                         因为 ClientActionManager 需要在不同的 async 上下文中
                         通过 .get() 获取当前有效的队列。
    """

    def __init__(self, parent_queue_ctx: contextvars.ContextVar | None = None):
        # {request_id: Future} —— 等待中的请求。
        # Future 由 request() 创建，由 resolve() 完成。
        self._pending: dict[str, asyncio.Future] = {}
        self._parent_queue_ctx = parent_queue_ctx

    async def request(self, action: str, params: dict | None = None,
                      timeout: float = 10.0) -> dict:
        """向客户端发起动作请求，等待并返回结果。

        Args:
            action:  动作名（"get_location" / "choose_option" / "confirm" 等）
            params:  动作参数
            timeout: 超时秒数，超时返回 {"error": "客户端操作超时"}

        Returns:
            客户端返回的结果 dict，格式由前端决定。
        """
        rid = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = future

        # 推送 client_action_request 到当前上下文的前端
        if self._parent_queue_ctx:
            queue = self._parent_queue_ctx.get(None)
            if queue:
                tc_id = current_tool_call_id.get()
                await queue.put({
                    "type": "client_action_request",
                    "action": action,
                    "request_id": rid,
                    "params": params or {},
                    "tool_call_id": tc_id,
                })

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return {"error": "客户端操作超时"}
        except Exception as e:
            self._pending.pop(rid, None)
            return {"error": str(e)}

    def resolve(self, request_id: str, result: dict) -> bool:
        """前端发回 client_action_result 时调用。

        Args:
            request_id: 请求 ID（与 request() 返回的 rid 匹配）
            result:     前端返回的结果 dict

        Returns:
            True 表示成功唤醒等待的 Future，False 表示 request_id 不存在（已超时或重复）。
        """
        fut = self._pending.pop(request_id, None)
        if fut and not fut.done():
            fut.set_result(result)
            return True
        return False
