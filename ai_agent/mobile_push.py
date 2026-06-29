"""
手机推送模块 —— 统一的手机端消息广播通道。

所有需要向手机推送消息的模块（剪贴板监听、通知工具等）都通过此模块发送。

v4.1 重构：将模块级可变集合封装为 MobileConnectionPool 类。
v4.2 统一：模块级函数委托给容器的唯一实例，消除双实例不一致风险。
"""
import time
from fastapi import WebSocket


class MobileConnectionPool:
    """手机 WebSocket 连接池：管理全局连接（global 端点）的注册/注销/广播。

    由 AppContainer 持有唯一实例，所有模块级函数委托至此。
    """

    def __init__(self):
        self._clients: set[WebSocket] = set()
        # 调试元数据：记录每条连接的来源路径和连接时间
        self._meta: dict[WebSocket, dict] = {}

    def register(self, ws: WebSocket, path: str = "") -> None:
        """注册移动客户端（global 连接建立时调用）。"""
        self._clients.add(ws)
        self._meta[ws] = {"path": path, "connected_at": time.time()}

    def unregister(self, ws: WebSocket) -> None:
        """注销移动客户端（连接断开时调用）。"""
        self._clients.discard(ws)
        self._meta.pop(ws, None)

    def is_online(self) -> bool:
        """检查是否有手机端在线。"""
        return len(self._clients) > 0

    def debug_info(self) -> list[dict]:
        """调试：返回所有连接的来源路径和连接时间。"""
        return [
            {"path": self._meta.get(ws, {}).get("path", "?"),
             "connected_at": self._meta.get(ws, {}).get("connected_at", 0)}
            for ws in self._clients
        ]

    async def broadcast(self, data: dict) -> int:
        """向所有已注册移动客户端广播消息。返回成功发送的设备数。

        自动清理断线连接。
        """
        dead = []
        count = 0
        for ws in list(self._clients):
            try:
                await ws.send_json(data)
                count += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
            self._meta.pop(ws, None)
        return count


# ═══════════════════════════════════════════════════════════════
# 模块级委托函数已移除（v4.2+ 统一通过 get_container().mobile_pool 访问）
# ═══════════════════════════════════════════════════════════════
