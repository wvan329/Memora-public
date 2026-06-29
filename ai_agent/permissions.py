"""
统一权限策略引擎。

替代过去散落在 ai_config.py / browser_task.py / delegate_tool.py 中的：
  - delegate_depth contextvar
  - tool_whitelist contextvar
  - tool_blacklist contextvar
  - allow_playwright contextvar
  - ai_config.ai_response() 中几十行 if-else

用法：
  from ai_agent.permissions import current_role, resolve_tools

  current_role.set("delegate")
  tools = resolve_tools(current_role.get(), TOOLS)
"""

import contextvars
from dataclasses import dataclass, field

# ── 角色 contextvar（替代 delegate_depth + tool_whitelist + allow_playwright）──
current_role: contextvars.ContextVar[str] = contextvars.ContextVar(
    'current_role', default='main'
)

# ── 当前工具调用 ID。
# 由 ConversationTurn._execute_tools 在调用每个工具前设置，
# SubTaskRunner / vision_tool 读取此值关联 chunk 到具体的工具卡片。
current_tool_call_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'tool_call_id', default=""
)

# ── 当前会话 ID。
# 由 ConversationTurn.execute() 在每轮对话开始时设置，
# 工具函数及子模块通过此变量获取当前会话 ID。
current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'session_id', default=""
)

# ── 浏览器 session ID（browser_task → playwright 跨 ConversationTurn 传递）。
# browser_task 设置后，ConversationTurn 会覆盖 current_session_id，
# 但此变量不会被覆盖，playwright 从中读取目标浏览器 session。
# 默认 "__unset__" 表示未被 browser_task 设置（如主 AI 直接调用 playwright）。
browser_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'browser_session_id', default="__unset__"
)

# ── 父级 SSE Queue（子 AI 通过它将 chunk 实时推送到主 AI 窗口）──
parent_queue: contextvars.ContextVar = contextvars.ContextVar(
    'parent_queue', default=None
)


@dataclass
class RoleConfig:
    """角色的工具权限配置"""
    allow_all: bool = False  # True = 拥有所有工具
    allow: set = field(default_factory=set)  # 白名单（allow_all=False 时生效）
    deny: set = field(default_factory=set)  # 黑名单（始终生效）


# ── 角色权限表 ──
ROLES: dict[str, RoleConfig] = {
    "main": RoleConfig(
        allow_all=True,
        deny=set(),
    ),
    "delegate": RoleConfig(
        allow_all=True,
        deny={"playwright", "ask_user", "schedule_restart",
              "add_reminder", "remove_reminder", "list_reminders","install_apk"},
        # 子 AI 不应弹出用户交互弹窗，不应直接重启服务，不应管理手机提醒
    ),
    "browser_inner": RoleConfig(
        allow={"playwright", "file_operation", "subprocess_exec",
               "python_exec", "wait"},
        deny={"ask_user", "schedule_restart","install_apk"},
    ),
    "compress": RoleConfig(
        allow_all=False,
        allow=set(),
        deny=set(),
        # compress 角色不应调用任何工具，直接生成文本回复
    ),
}


def resolve_tools(role: str, all_tools: list) -> list:
    """给定角色名 → 返回该角色可用的工具列表（去重）。

    为什么需要去重？
    正常情况下 ToolRegistry 保证工具名唯一，但 MCP 重连等边缘场景
    可能导致注册表中出现同名工具。此防御层确保即使源头漏了，
    也不会把重复工具传给 AI API（否则 API 会返回 "Tool names must be unique"）。

    Args:
        role: 角色名（"main" / "delegate" / "browser_inner"）
        all_tools: 全部注册的工具列表

    Returns:
        过滤后的工具列表（保证 function.name 唯一）
    """
    cfg = ROLES.get(role)
    if cfg is None:
        # 未知角色 → 保守策略：不给工具
        return []

    if cfg.allow_all:
        tools = list(all_tools)
    else:
        tools = [t for t in all_tools if t["function"]["name"] in cfg.allow]

    tools = [t for t in tools if t["function"]["name"] not in cfg.deny]

    # 防御层：按 function.name 去重，保留最后一次出现的（即最新注册的）
    seen = {}
    for t in tools:
        seen[t["function"]["name"]] = t
    return list(seen.values())
