"""
通知模式开关 —— 开启后每次 AI 回复完成推送手机通知。

开关状态持久化到 .env 文件的 NOTIFICATION_ENABLED 字段，重启不丢失。
"""
from ai_agent.settings import settings


def _persist_notification_enabled(value: bool) -> None:
    """将通知开关写入 .env 文件。"""
    from ai_agent.settings import set_env_bool
    set_env_bool('NOTIFICATION_ENABLED', value)


def is_notification_enabled() -> bool:
    """供 message_router 在 AI 完成后检查是否应推送通知。"""
    return settings.notification_enabled
