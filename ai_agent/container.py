"""
依赖注入容器 —— 创建并装配所有核心组件。

使用方法：
    container = AppContainer()
    await container.init()    # 异步初始化（DB + 工具加载）
    set_container(container)  # 注册全局单例

    # 后续通过 get_container() 获取
    c = get_container()
    c.session_mgr.broadcast(...)

依赖层级（从底往上）：
    第 1 层（零依赖）：  MessageRepository, ToolRegistry, SkillIndexManager, ToolIndexManager
    第 2 层（依赖 1）：  SessionManager, ClientActionManager, MobileConnectionPool
    第 3 层（依赖 2）：  SubTaskRunner, ConversationTurn, MessageRouter
"""
import contextvars

from ai_agent.settings import settings
from ai_agent.repository import MessageRepository
from ai_agent.tool_registry import ToolRegistry
from ai_agent.skill_index import SkillIndexManager
from ai_agent.tool_index import ToolIndexManager
from ai_agent.session_manager import SessionManager
from ai_agent.client_action import ClientActionManager
from ai_agent.mobile_push import MobileConnectionPool
from ai_agent.sub_task import SubTaskRunner
from ai_agent.conversation import ConversationTurn
from ai_agent.message_router import MessageRouter
from ai_agent.permissions import parent_queue, current_role
from ai_agent.permissions import current_session_id


class AppContainer:
    """应用容器：创建所有核心组件并注入依赖。

    由 main.py 的 lifespan 创建，整个应用生命周期只有一个实例。
    公开所有组件为属性，MessageRouter 和 HTTP 路由通过 get_container() 访问。
    """

    def __init__(self):
        # ── 第 1 层：零依赖 ──
        self.repo = MessageRepository(settings.db_path)
        self.tool_registry = ToolRegistry()
        self.skill_index = SkillIndexManager()
        self.tool_index = ToolIndexManager()

        # ── 第 2 层：依赖第 1 层 ──
        self.session_mgr = SessionManager()
        self.client_action = ClientActionManager(parent_queue_ctx=parent_queue)
        self.mobile_pool = MobileConnectionPool()

        # SubTaskRunner 依赖所有第 1、2 层组件
        self.sub_task_runner = SubTaskRunner(
            session_mgr=self.session_mgr,
            repo=self.repo,
            tool_registry=self.tool_registry,
            client_action=self.client_action,
            parent_queue_ctx=parent_queue,
        )

        # ── 第 3 层：依赖第 2 层 ──
        # MessageRouter 处理所有 WebSocket 消息分发
        self.message_router = MessageRouter(
            session_mgr=self.session_mgr,
            client_action=self.client_action,
            repo=self.repo,
            mobile_pool=self.mobile_pool,
            conversation_factory=self._create_conversation,
        )

    def _create_conversation(self, sid: str, prompt: str, role: str) -> ConversationTurn:
        """工厂方法：创建一轮对话实例。

        Args:
            sid:    会话 ID
            prompt: 用户输入
            role:   角色（"main" / "delegate" / "browser_inner" / "compress"）
        """
        return ConversationTurn(
            session_id=sid,
            prompt=prompt,
            role=role,
            repo=self.repo,
            tool_registry=self.tool_registry,
            session_mgr=self.session_mgr,
        )

    async def init(self) -> None:
        """异步初始化：DB 建表、加载工具、生成技能索引 + 系统提示词。

        ToolIndexManager.load_all() 会 import shared/tool/ + local/tool/ 下所有模块，
        触发 @tool 装饰器将工具直接注册到 self.tool_registry。

        MCP 工具由 main.py 中的 register_mcp_tools() 独立加载（后台常驻连接）。
        """
        await self.repo.init()

        # 工具加载（shared/tool/ + local/tool/）
        self.tool_index.load_all(self.tool_registry)

        # 技能索引 + 系统提示词加载
        self.skill_index.load_all_prompts(settings)


# ── 全局单例 ──
# main.py 的 lifespan 中创建并 set，后续所有模块通过 get_container() 获取。
_container: AppContainer | None = None


def get_container() -> AppContainer:
    """获取全局容器实例。

    Raises:
        RuntimeError: 容器尚未创建（在 lifespan 之前或之外调用）
    """
    if _container is None:
        raise RuntimeError("AppContainer 尚未创建，请在 lifespan 中初始化")
    return _container


def set_container(container: AppContainer) -> None:
    """设置全局容器实例（main.py lifespan 启动时调用一次）。"""
    global _container
    _container = container
