"""
AI 配置 —— OpenAI 客户端 + AI 调用函数。

v4.1 重构：移除模块级 TOOLS / FUNC_TOOL_MAP / FUNC_SESSION_MAP 全局变量。
工具注册统一由 ToolRegistry 管理，ai_response 通过容器获取工具列表。
"""
from openai import AsyncOpenAI

from .settings import settings
from .permissions import current_role, resolve_tools


# ── AI 客户端 ──
ali_client = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
ali_bailian_client = AsyncOpenAI(api_key=settings.ali_api_key, base_url=settings.ali_bailian_base_url)


# ── 辅助：获取工具列表 ──

def _get_tools() -> list[dict]:
    """获取当前角色可用的工具列表（从容器 ToolRegistry 获取后按角色过滤）。"""
    from .container import get_container
    all_tools = get_container().tool_registry.tools
    return resolve_tools(current_role.get(), all_tools)


async def ai_response(messages, model: str | None = None):
    """流式调用 AI，返回工具调用信息。

    工具列表从 ToolRegistry 动态获取，确保 MCP 工具在运行时注册后也能被 AI 调用。
    """
    tools = _get_tools()

    completion = await ali_client.chat.completions.create(
        model=model or settings.model_name,
        messages=messages, tools=tools, stream=True,
        reasoning_effort="max",
    )
    reasoning_content = []
    is_answering = False
    is_tool_call = False
    tool_calls = {}
    async for chunk in completion:
        delta = chunk.choices[0].delta
        delta_tool_calls = delta.tool_calls
        if delta_tool_calls:
            for tc_chunk in delta_tool_calls:
                if not is_tool_call:
                    is_tool_call = True
                idx = tc_chunk.index
                tc_chunk.function.arguments = tc_chunk.function.arguments or ""
                if idx not in tool_calls:
                    tool_calls[idx] = tc_chunk
                else:
                    tool_calls[idx].function.arguments += tc_chunk.function.arguments
                tc_key = tool_calls[idx].id if tool_calls[idx].id else f"idx_{idx}"
                if tc_chunk.function.name:
                    yield {"type": "tool_call_name", "tool_call_id": tc_key, "tool_call_index": idx, "content": tc_chunk.function.name}
                if tc_chunk.function.arguments:
                    yield {"type": "tool_call_args", "tool_call_id": tc_key, "tool_call_index": idx, "content": tc_chunk.function.arguments}
        else:
            delta = chunk.choices[0].delta
            if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
                if not is_answering:
                    reasoning_content.append(delta.reasoning_content)
                    yield {"type": "reason", "content": delta.reasoning_content}
            if hasattr(delta, "content") and delta.content:
                if not is_answering:
                    is_answering = True
                yield {"type": "text", "content": delta.content}
    yield {"tool_calls": tool_calls or None, "reasoning": "".join(reasoning_content)}


async def ai_vision_response(image_urls: list[str], question: str, *,
                             model: str | None = None, vl_high_res: bool = False):
    """流式调用百炼视觉模型。"""
    extra = {}
    if vl_high_res:
        extra["vl_high_resolution_images"] = True

    content_parts = [{"type": "text", "text": question}]
    for url in image_urls:
        content_parts.insert(0, {"type": "image_url", "image_url": {"url": url}})

    completion = await ali_bailian_client.chat.completions.create(
        model=model or settings.ali_vision_model,
        messages=[{"role": "user", "content": content_parts}],
        stream=True, extra_body=extra if extra else None,
    )
    reasoning_content = []
    is_answering = False
    async for chunk in completion:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
            if not is_answering:
                reasoning_content.append(delta.reasoning_content)
                yield {"type": "reason", "content": delta.reasoning_content}
        if hasattr(delta, "content") and delta.content:
            if not is_answering:
                is_answering = True
            yield {"type": "text", "content": delta.content}
    yield {"reasoning": "".join(reasoning_content)}


# ── 高频一次性调用（deepseek-v4-chat）──
DEFAULT_CHAT_MODEL = "deepseek-v4-flash"

async def ai_chat(prompt: str, *, enable_thinking: bool = False,
                  model: str | None = None) -> dict:
    """轻量级流式 AI 调用，无工具，消息列表只有一条用户消息。

    适用于高频、一次性任务（如摘要、翻译、分类等）。

    Args:
        prompt: 用户消息内容
        enable_thinking: 是否开启思考模式（开启时自动设置 reasoning_effort='max'）
        model: 模型名，默认 deepseek-v4-chat

    Yields:
        dict: 流式 chunk，格式同 ai_response（含 type/text/reason 字段）
    """
    messages = [{"role": "user", "content": prompt}]
    extra_body = {}
    create_kwargs: dict = {
        "model": model or DEFAULT_CHAT_MODEL,
        "messages": messages,
        "stream": True,
    }

    if enable_thinking:
        create_kwargs["reasoning_effort"] = "max"
    else:
        extra_body["thinking"] = {"type": "disabled"}

    if extra_body:
        create_kwargs["extra_body"] = extra_body

    completion = await ali_client.chat.completions.create(**create_kwargs)
    is_answering = False

    async for chunk in completion:
        delta = chunk.choices[0].delta
        if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
            if not is_answering:
                yield {"type": "reason", "content": delta.reasoning_content}
        if hasattr(delta, "content") and delta.content:
            if not is_answering:
                is_answering = True
            yield {"type": "text", "content": delta.content}
