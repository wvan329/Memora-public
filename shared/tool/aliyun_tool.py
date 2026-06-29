# tools/aliyun_tool.py
"""
阿里云 ECS 工具 — 通过阿里云 OpenAPI SDK 管理 ECS 实例。
凭据从 .env 读取（ALI_ACCESS_KEY_ID / ALI_ACCESS_KEY_SECRET / ALI_ECS_REGION / ALI_ECS_INSTANCE_ID）。
"""
from typing import Annotated
from pydantic import Field
from ai_agent.utils import tool
from ai_agent.settings import settings


@tool("""强制重启阿里云 ECS 服务器 — 调用阿里云 RebootInstance API 强制重启指定的 ECS 实例。
服务器卡死、SSH 无法连接时也能执行（控制面操作，不依赖实例自身状态）。
重启通常需要 1 分钟，期间服务器不可达。""")
async def reboot_ecs(
    # force: Annotated[bool, Field(description="是否强制重启（相当于拔电源再插回去），默认 False 正常重启")] = False,
) -> str:
    try:
        from aliyunsdkcore.client import AcsClient
        from aliyunsdkecs.request.v20140526.RebootInstanceRequest import RebootInstanceRequest

        ak_id = settings.ali_access_key_id
        ak_secret = settings.ali_access_key_secret
        region = settings.ali_ecs_region
        instance_id = settings.ali_ecs_instance_id

        if not ak_id:
            return "❌ 未配置 ALI_ACCESS_KEY_ID，请在 .env 中设置"
        if not instance_id:
            return "❌ 未配置 ALI_ECS_INSTANCE_ID，请在 .env 中设置"

        client = AcsClient(ak_id, ak_secret, region)
        req = RebootInstanceRequest()
        req.set_InstanceId(instance_id)
        force = True
        req.set_ForceStop("true" if force else "false")

        client.do_action_with_exception(req)

        mode = "强制重启" if force else "正常重启"
        return f"✅ 已发送{mode}请求，实例 {instance_id}（{region}）将在 1~2 分钟内完成重启。"

    except Exception as e:
        return f"❌ 重启失败 [{type(e).__name__}]: {e}"
