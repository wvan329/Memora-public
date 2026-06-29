"""
对话轮次 —— 封装一轮 AI 对话的完整生命周期。

替代旧 run.py 中 131 行的神函数 run()。

生命周期阶段：
    1. 加载历史 + system prompt
    2. 用户消息入库 + 广播
    3. AI 调用 → 工具循环（可能多轮）
    4. 最终文本入库 + stream_end

所有输出通过 parent_queue ContextVar 推送，不直接操作 WebSocket。

为什么使用 ContextVar 传递 parent_queue 而不是显式参数？
ContextVar 可以在 async 任务链中自动跨函数传递，无需每层显式传参。
ConversationTurn 的调用者（SessionManager.run_turn）在创建 asyncio.Task 前
通过 parent_queue.set(queue) 设置，ConversationTurn.execute() 通过
parent_queue.get() 获取。如果 ContextVar 未设置，execute() 会抛出 RuntimeError
以尽早暴露调用错误。

abort 处理（v4.3 重构）：
asst_msg（含 tool_calls）在工具并行执行前入库，每个工具结果独立入库。
当 abort 发生时（CancelledError），AbortHandler 扫描最后一条
assistant 消息中未完成的工具调用，补入 "用户主动终止"。
这确保：刷新页面后工具调用信息完整（有 tool_call 必有 tool_result）。

v5.0 重构：
- TurnState dataclass 封装可变状态，替代可变容器传引用模式
- ToolExecutionOrchestrator 负责工具执行 + 入库 + placeholder 管理
- AbortHandler 负责 abort 时三个时刻（A/B/C）的补全逻辑
- ConversationTurn 只保留生命周期编排
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ai_agent.ai_config import ai_response
from ai_agent.early_stop import _detect_early_stop, _handle_early_stop
from ai_agent.permissions import current_role, parent_queue, current_tool_call_id, current_session_id


class _SafeDict(dict):
    """format_map 专用：缺失的键原样保留 {key} 而不是抛 KeyError。"""
    def __missing__(self, key):
        return '{' + key + '}'
from ai_agent.settings import settings

if TYPE_CHECKING:
    from ai_agent.repository import MessageRepository
    from ai_agent.tool_registry import ToolRegistry

# 工具调用结果最大字符数。超过此值截断前 20000 字符，
# 防止单次工具返回过大导致 context window 溢出。
MAX_TOOL_RESULT_CHARS = 20000

# 不受截断限制的工具——这些工具的返回结果对 AI 后续推理至关重要，
# 截断会丢失关键信息（如子AI结果、浏览器页面内容、图像理解结果）。
UNTRUNCATED_TOOLS = frozenset({
    "ai_delegate",
    "browser_task",
    "vision_understand",
})


# ═══════════════════════════════════════════════════════════════
# TurnState — 一轮对话中的可变状态
# ═══════════════════════════════════════════════════════════════

@dataclass
class TurnState:
    """一轮 AI 对话中的可变状态，替代原来的可变容器传引用模式。

    原来通过三个独立的 list/dict 传引用（answer_chunks, reasoning_buf, tool_call_buf），
    调用者需了解内部结构（如 reasoning_buf[0] 是单元素列表）。
    现在统一封装为 dataclass，语义清晰，类型安全。
    """

    text_chunks: list[str] = field(default_factory=list)
    """AI 流式输出中已收到的文本 chunk 列表。"""

    reasoning: str = ""
    """AI 流式输出中已收到的思考内容（DeepSeek R1 等模型的 reasoning_content）。"""

    pending_tool_calls: dict[str, dict] = field(default_factory=dict)
    """流式阶段累积的工具调用片段。
    格式：{tool_call_id: {"name": str, "args": str, "index": int}}
    当 AI 返回完整的 tool_calls 时被清空。
    """

    def reset(self) -> None:
        """重置所有状态，在新的 _ai_call 开始前调用。"""
        self.text_chunks.clear()
        self.reasoning = ""
        self.pending_tool_calls.clear()

    @property
    def text(self) -> str:
        """将 text_chunks 拼接为完整文本。"""
        return "".join(self.text_chunks)


# ═══════════════════════════════════════════════════════════════
# ToolExecutionOrchestrator — 工具并行执行编排器
# ═══════════════════════════════════════════════════════════════

class ToolExecutionOrchestrator:
    """工具并行执行编排器：执行工具、入库 assistant 消息、管理 placeholder、补填终止标记。

    职责：
    - execute_tools: 并行执行一批工具调用，检测 early_stop
    - _invoke_single_tool: 调用单个工具 + 结果截断
    - _save_assistant_msg: 助手消息入库
    - _save_tool_placeholders: 预写 pending placeholder
    - _fill_tool_results: 为未完成工具补 aborted 标记

    依赖（通过构造器注入）：
        repo:           消息持久化
        tool_registry:  工具注册中心（用于 invoke 工具）
        session_id:     当前会话 ID
        turn_id:        当前轮次 ID
    """

    def __init__(self, repo: MessageRepository, tool_registry: ToolRegistry,
                 session_id: str, turn_id: str):
        self.repo = repo
        self.tool_registry = tool_registry
        self.session_id = session_id
        self.turn_id = turn_id

    # ── 工具执行主入口 ──────────────────────────────────────

    async def execute_tools(self, tool_calls: dict, messages: list[dict],
                            q: asyncio.Queue, reasoning: str = "",
                            answer_chunks: list[str] | None = None,
                            ) -> tuple[list[dict], dict | None]:
        """并行执行一批工具调用，入库助手消息和工具结果，广播结果到前端。

        asst_msg（含 tool_calls）在工具执行前入库；
        每个工具结果独立入库（而非批量事务），确保 abort 时已完成工具不丢失。

        Args:
            tool_calls:    ai_response 返回的 tool_calls dict
            messages:      当前消息列表（追加助手消息 + 工具结果）
            q:             输出队列
            reasoning:     上轮 AI 调用返回的思考内容
            answer_chunks: AI 调用工具前输出的文本（如「好的我来搜」），
                           消费后清空；None 表示无文本

        Returns:
            (results, early_stop): 工具结果列表 + 提前终止标记（None 表示正常）
        """
        tc_list = list(tool_calls.values())
        tool_calls_data = [tc.model_dump() for tc in tc_list]

        # 工具调用前 AI 可能说了话（如「好的我来搜」），
        # 放入 asst_msg.content 与 tool_calls 共存（OpenAI 允许）。
        pre_text = "".join(answer_chunks).strip() if answer_chunks else ""
        if answer_chunks:
            answer_chunks.clear()

        # asst_msg 提前入库：即使后续工具执行中被 abort，
        # AbortHandler 可据此补全缺失的 tool_result。
        await self._save_assistant_msg(pre_text or None, reasoning or None,
                                        tool_calls_data, messages)

        # 入库后立即清空流式阶段缓存（tool_call_name/args），避免 subscribe
        # 时 chunk 快照与 history 中的完整数据重复拼接，导致 args 显示两份。
        q.put_nowait({"type": "__flush_cache__"})

        # 为所有 tool_call 预写 placeholder，确保 crash/重启 后
        # DB 中 tool_calls 与 tool_result 永远 1:1（不再出现 orphaned tool_calls）。
        await self._save_tool_placeholders(tool_calls_data, messages)

        # 并行执行所有工具。
        # 每个工具执行完后立即 UPDATE placeholder → 真实结果 + 广播。
        async def _run_one(tc, idx: int) -> dict:
            """执行单个工具 → 立即入库 → 立即广播。"""
            tc_dict = tc.model_dump()
            tc_key = tc.id if tc.id else f"idx_{idx}"
            current_tool_call_id.set(tc_dict["id"])

            try:
                result = await self._invoke_single_tool(tc_dict)
            except asyncio.CancelledError:
                # abort：不更新 placeholder，留给 _fill_tool_results 处理
                return {"role": "tool", "tool_call_id": tc_dict["id"], "content": ""}
            except BaseException as e:
                result = {
                    "role": "tool",
                    "tool_call_id": tc_dict["id"],
                    "content": f"工具执行异常：{e}",
                }

            content = result.get("content")

            # 更新 DB 中的 placeholder 为真实结果
            await self.repo.update_tool_result(tc_key, content)

            # 同步更新内存中的 placeholder
            for _m in messages:
                if _m.get("role") == "tool" and _m.get("tool_call_id") == tc_key:
                    _m["content"] = content
                    break

            # 立即广播到前端
            q.put_nowait({
                "type": "tool_result",
                "tool_call_id": tc_key,
                "tool_call_index": idx,
                "content": content,
            })

            return result

        tasks = [_run_one(tc, idx) for idx, tc in tool_calls.items()]
        results = await asyncio.gather(*tasks)

        # 检查提前终止标记（restart / compress）
        early_stop = _detect_early_stop(results)

        q.put_nowait({"type": "__flush_cache__"})
        return results, early_stop

    # ── 单个工具调用 ────────────────────────────────────────

    async def _invoke_single_tool(self, tc_dict: dict) -> dict:
        """执行单个工具调用并返回标准化结果。

        内部调用 ToolRegistry.invoke，负责 JSON 解析、异常兜底、结果截断。

        Args:
            tc_dict: 工具调用的 model_dump()，含 id + function.name + function.arguments

        Returns:
            {"role": "tool", "tool_call_id": ..., "content": ...}
        """
        try:
            arguments = json.loads(tc_dict["function"]["arguments"])
            content = await self.tool_registry.invoke(tc_dict["function"]["name"], arguments)
        except asyncio.CancelledError:
            raise  # 取消信号必须传播，让上层 abort 处理补全入库
        except Exception as e:
            content = f"工具调用失败：{e}"

        content = self.tool_registry._ensure_str(content)

        # 防止工具返回过大导致 context window 溢出
        # 白名单工具（ai_delegate/browser_task/vision_understand）不截断
        tool_name = tc_dict["function"]["name"]
        if len(content) > MAX_TOOL_RESULT_CHARS and tool_name not in UNTRUNCATED_TOOLS:
            original_len = len(content)
            content = content[:MAX_TOOL_RESULT_CHARS]
            content += (f"\n\n[结果过长已截断：原始 {original_len} 字符，"
                        f"仅保留前 {MAX_TOOL_RESULT_CHARS} 字符]")

        return {
            "role": "tool",
            "tool_call_id": tc_dict["id"],
            "content": content,
        }

    # ── 持久化辅助方法 ──────────────────────────────────────

    async def _save_assistant_msg(self, content: str | None, reasoning: str | None,
                                   tool_calls: list | None, messages: list[dict]) -> None:
        """入库一条 assistant 消息到 DB + 内存列表。"""
        msg: dict = {
            "role": "assistant",
            "content": content or None,
            "reasoning_content": reasoning or None,
            "user_id": self.session_id,
            "turn_id": self.turn_id,
        }
        if tool_calls:
            msg["tool_calls"] = tool_calls
        messages.append(msg)
        await self.repo.save(msg)

    async def _save_tool_placeholders(self, tool_calls_data: list, messages: list[dict]) -> None:
        """为每条 tool_call 预写 pending placeholder。

        assistant msg 入库后立即调用，确保 crash/重启 时
        tool_calls 与 tool_result 永远 1:1 存在于 DB 中。
        工具完成后通过 update_tool_result 替换 pending → 真实结果。
        """
        pending_content = json.dumps(
            {"pending": True, "message": "工具执行中…"},
            ensure_ascii=False,
        )
        for tc in tool_calls_data:
            tc_id = tc.get("id", "")
            if not tc_id:
                continue
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": pending_content,
                "user_id": self.session_id,
                "turn_id": self.turn_id,
            }
            await self.repo.save(tool_msg)
            messages.append(tool_msg)

    async def fill_tool_results(self, tcs: list, messages: list[dict], q: asyncio.Queue) -> None:
        """为仍在 pending 的 tool_call 补 aborted 终止标记。

        新方案：tool_msg 已在 _save_tool_placeholders 中预写为 pending。
        此处只标记还在 pending 状态的 placeholder 为 aborted。
        注意：此方法是同步的（在 AbortHandler 中通过 run_coroutine_threadsafe 调用时），
        内部使用 asyncio.get_running_loop() 安全执行 DB 更新。
        """
        aborted_content = json.dumps(
            {"aborted": True, "message": "用户主动终止"},
            ensure_ascii=False,
        )
        PENDING_PREFIX = '{"pending":'
        for tc in tcs:
            tc_id = tc.get("id", "")
            if not tc_id:
                continue
            # 只更新仍为 pending 的 placeholder（已完成工具已有真实结果，不覆盖）
            existing = next((m for m in messages
                           if m.get("role") == "tool" and m.get("tool_call_id") == tc_id), None)
            if existing is None:
                continue
            if existing.get("content", "") and not existing["content"].startswith(PENDING_PREFIX):
                continue  # 已有真实结果，跳过

            await self.repo.update_tool_result(tc_id, aborted_content)
            existing["content"] = aborted_content
            q.put_nowait({
                "type": "tool_result",
                "tool_call_id": tc_id,
                "content": aborted_content,
            })

    @staticmethod
    def get_last_tool_calls(messages: list[dict]) -> list:
        """从 messages 中找到最后一个含 tool_calls 的 asst_msg，返回 tool_calls 列表。"""
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                tcs = m["tool_calls"]
                if isinstance(tcs, str):
                    try:
                        tcs = json.loads(tcs)
                    except Exception:
                        return []
                return tcs if isinstance(tcs, list) else []
        return []


# ═══════════════════════════════════════════════════════════════
# AbortHandler — abort 补全处理器
# ═══════════════════════════════════════════════════════════════

class AbortHandler:
    """abort 发生时补全：已流式输出的内容原样入库，缺失的 tool_result 补终止标记。

    原则：「是什么就是什么」——AI 说了多少就存多少，不加伪造文本。
    工具结果补 {"aborted": true} 供前端 ToolCard 渲染。

    三个截断时刻：
      时刻 A — 工具参数流式中被终止：构造 tcs_data → 入库 asst_msg + 补 result
      时刻 B — 纯文本/思考中被终止：入库纯文本 asst_msg
      时刻 C — 工具执行中被终止：asst_msg 已入库，补缺失 result

    依赖（通过构造器注入）：
        repo:           消息持久化
        tool_executor:  ToolExecutionOrchestrator（复用其持久化方法）
        session_id:     当前会话 ID
        turn_id:        当前轮次 ID
    """

    def __init__(self, repo: MessageRepository,
                 tool_executor: ToolExecutionOrchestrator,
                 session_id: str, turn_id: str):
        self.repo = repo
        self._exec = tool_executor
        self.session_id = session_id
        self.turn_id = turn_id

    async def complete_aborted(self, messages: list[dict], q: asyncio.Queue,
                                turn_state: TurnState) -> None:
        """abort 发生时调用：根据 TurnState 判断当前处于哪个时刻，执行相应补全。"""
        reasoning_text = turn_state.reasoning
        text = turn_state.text

        # ── 时刻 A：工具参数流式输出中被终止 ——
        pending_tcs = turn_state.pending_tool_calls
        if pending_tcs:
            tcs_data = [{
                "id": tid,
                "type": "function",
                "function": {"name": info["name"], "arguments": info["args"]},
            } for tid, info in pending_tcs.items()]
            await self._exec._save_assistant_msg(text, reasoning_text, tcs_data, messages)
            await self._exec._save_tool_placeholders(tcs_data, messages)
            await self._exec.fill_tool_results(tcs_data, messages, q)
            q.put_nowait({"type": "__flush_cache__"})
            q.put_nowait({"type": "stream_end", "final_text": ""})
            return

        # ── 时刻 B：纯文本/思考被终止（无 tool_call）──
        # content 不能为空（API 要求 content 或 tool_calls 至少一个非空；
        # reasoning_content 是附加字段，不满足此要求）。text 为空时用 reasoning 兜底。
        if text or reasoning_text:
            await self._exec._save_assistant_msg(
                text or reasoning_text, reasoning_text, None, messages)

        # ── 时刻 C：asst_msg 已入库，补缺失的 tool_result ──
        tcs = ToolExecutionOrchestrator.get_last_tool_calls(messages)
        if tcs:
            await self._exec.fill_tool_results(tcs, messages, q)

        q.put_nowait({"type": "__flush_cache__"})
        q.put_nowait({"type": "stream_end", "final_text": ""})


# ═══════════════════════════════════════════════════════════════
# ConversationTurn — 一轮 AI 对话（生命周期编排）
# ═══════════════════════════════════════════════════════════════

class ConversationTurn:
    """一轮 AI 对话，只负责生命周期编排，具体执行委派给子模块。

    依赖（全部通过构造器注入）：
        repo:           消息持久化
        tool_registry:  工具注册表（用于 invoke 工具）
        session_mgr:    会话管理器（不直接使用，传给子模块）

    注意：parent_queue 通过 ContextVar 传入（由外部在调用 execute() 前设置），
    而非构造器参数，因为 ContextVar 可以在 async 任务链中自动传递。
    """

    def __init__(self, session_id: str, prompt: str, role: str,
                 repo: MessageRepository, tool_registry: ToolRegistry,
                 session_mgr) -> None:
        self.session_id: str = session_id
        self.prompt: str = prompt
        self.role: str = role  # "main" / "delegate" / "browser_inner" / "compress"
        self.repo = repo
        self.tool_registry = tool_registry
        self.session_mgr = session_mgr
        self.turn_id: str = str(uuid.uuid4())

        # 子模块：工具执行编排 + abort 补全
        self._tool_exec = ToolExecutionOrchestrator(
            repo, tool_registry, session_id, self.turn_id)
        self._abort_handler = AbortHandler(
            repo, self._tool_exec, session_id, self.turn_id)

    # ═══════════════════════════════════════════════════════════════
    # 生命周期主入口
    # ═══════════════════════════════════════════════════════════════

    async def execute(self) -> None:
        """执行完整一轮对话。

        所有输出通过 parent_queue.get() 推送，由外部消费者（SessionManager.consume_queue）
        负责缓存+广播。CancelledError 在此处捕获以补全未完成的工具调用。

        Raises:
            RuntimeError: parent_queue ContextVar 未设置（调用者未正确初始化）。
        """
        # 设置 ContextVar：工具调用时需要知道当前会话和角色
        current_role.set(self.role)
        current_session_id.set(self.session_id)

        q: asyncio.Queue = parent_queue.get()
        if q is None:
            raise RuntimeError("parent_queue 未设置，无法推送消息。"
                               "请在调用 execute() 前通过 parent_queue.set(queue) 设置。")

        # ── 阶段 1：加载历史 + system prompt（按角色选择不同提示词）──
        messages: list[dict] = await self.repo.load(self.session_id)
        if len(messages) == 0:
            if self.role == "compress":
                sp = ""
            else:
                _role_prompt_map = {
                    "main": settings.system_prompt,
                    "delegate": settings.system_prompt_delegate or settings.system_prompt,
                    "browser_inner": settings.system_prompt_browser or settings.system_prompt,
                }
                sp = _role_prompt_map.get(self.role, settings.system_prompt)
            if sp and sp.strip():
                sys_msg = {
                    "role": "system",
                    "content": sp.format_map(_SafeDict(
                        path=settings.work_dir, shared_dir=settings.shared_dir)),
                    "user_id": self.session_id,
                }
                messages.append(sys_msg)
                await self.repo.save(sys_msg)

        # ── 阶段 2：用户消息入库 + 广播 ──
        messages.append({
            "role": "user", "content": self.prompt, "turn_id": self.turn_id,
        })
        user_msg_data = {
            "role": "user", "content": self.prompt,
            "user_id": self.session_id, "turn_id": self.turn_id,
        }
        await self.repo.save(user_msg_data)

        q.put_nowait({
            "type": "user_message", "role": "user",
            "content": self.prompt, "turn_id": self.turn_id,
        })

        # ── 阶段 3 + 4：AI 调用 → 工具循环 → 收尾 ──
        state = TurnState()
        try:
            tool_calls = await self._ai_call(messages, q, state)

            while tool_calls:
                results, early_stop = await self._tool_exec.execute_tools(
                    tool_calls, messages, q, state.reasoning, state.text_chunks)

                if early_stop:
                    await _handle_early_stop(early_stop, self.session_id, q)
                    return

                tool_calls = await self._ai_call(messages, q, state)

            # ── 阶段 4：收尾 ──
            final_text = state.text
            if not final_text and state.reasoning:
                # DeepSeek 有时只有思考没有正文，把思考作为最终回复
                q.put_nowait({"type": "text", "content": state.reasoning})

            final_content = final_text or state.reasoning or ""
            await self.repo.save({
                "role": "assistant", "content": final_content,
                "reasoning_content": state.reasoning,
                "user_id": self.session_id, "turn_id": self.turn_id,
            })

            q.put_nowait({"type": "__flush_cache__"})
            q.put_nowait({"type": "stream_end", "final_text": final_text})

        except asyncio.CancelledError:
            # abort 时补全：将已流式输出的内容 + 终止标记入库
            await self._abort_handler.complete_aborted(messages, q, state)
            raise  # 重新抛出，让 run_turn 知道任务已被取消

    # ═══════════════════════════════════════════════════════════════
    # 内部：单次 AI 调用
    # ═══════════════════════════════════════════════════════════════

    async def _ai_call(self, messages: list[dict], q: asyncio.Queue,
                       state: TurnState) -> dict | None:
        """执行一次流式 AI 调用，逐 chunk 推送到 q。

        TurnState 在调用前 reset，流式过程中实时更新。
        abort 时 AbortHandler 可从 TurnState 读取已流式输出的内容入库。

        Args:
            messages: 当前消息列表（含历史）
            q:        输出队列
            state:    本轮可变状态（调用前会 reset）

        Returns:
            tool_calls dict，无工具时为 None
        """
        state.reset()
        tool_calls = None

        async for chunk in ai_response(messages):
            if "tool_calls" in chunk:
                tool_calls = chunk["tool_calls"]
                state.reasoning = chunk["reasoning"]
                state.pending_tool_calls.clear()  # 所有工具调用完成
                break
            ctype = chunk.get("type", "")
            if ctype == "text":
                state.text_chunks.append(chunk["content"])
            elif ctype == "reason":
                state.reasoning += chunk["content"]
            elif ctype == "tool_call_name":
                tc_id = chunk.get("tool_call_id", "")
                state.pending_tool_calls[tc_id] = {
                    "name": chunk["content"],
                    "args": "",
                    "index": chunk.get("tool_call_index", 0),
                }
            elif ctype == "tool_call_args":
                tc_id = chunk.get("tool_call_id", "")
                if tc_id in state.pending_tool_calls:
                    state.pending_tool_calls[tc_id]["args"] += chunk["content"]
            q.put_nowait(chunk)

        return tool_calls
