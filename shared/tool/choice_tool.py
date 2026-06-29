"""
用户交互工具 —— 统一确认框、选择框、输入框为一个工具。

前端弹出统一对话框，支持多页弹窗（左右滑动切换）：
- 选项按钮（page.options 不为空）
- 手动输入框（默认显示）
- 确认/取消按钮
- pages 数组决定页数，每页独立消息/选项/输入

通过 client_action 通用机制弹出对话框，等待用户操作后返回结果。
"""
from typing import Annotated, Any
from pydantic import BaseModel, Field
from ai_agent.utils import tool
from ai_agent.container import get_container


class AskPage(BaseModel):
    """弹窗的单页配置"""
    message: str = Field(description="提示信息，描述需要用户做什么")
    options: list[str] | None = Field(default=None, description="可选项列表，如 ['方案A', '方案B']")
    confirm_text: str = Field(default="确认", description="确认按钮文字")
    cancel_text: str = Field(default="取消", description="取消按钮文字")


@tool("高效的用户交互工具：支持多页弹窗（左右滑动切换），"
      "使用 pages 参数传入问题列表，前端渲染多页弹窗（左右滑动切换）。"
      "每页字段：message (提示文字), options? (选项列表，前端支持多选), confirm_text? (确认按钮，默认'确认'), cancel_text? (取消按钮，默认'取消')。"
      "能同时调用多个页面就调用多个页面——一次 ask_user 把多个问题放在 pages 数组里，效率远高于多次调用。"
      "适用场景：多项选择、确认操作、自定义输入、游戏互动、多步骤问卷、或以上组合。")
async def ask_user(
        pages: Annotated[list[AskPage], Field(
            description="弹窗页面列表，数组长度决定页数。单页时数组只含一个元素。"
        )],
):
    # pages 已被 tool wrapper 的 model_dump() 转为 dict 列表，直接透传
    result = await get_container().client_action.request("ask_user", {
        "pages": pages,
        "show_input": True,
    }, timeout=2000)

    if result.get("error"):
        # return f"[用户未响应: {result['error']}]"
        return f"[用户取消了操作]"

    if result.get("cancelled"):
        return "[用户取消了操作]"

    all_answers = result.get("answers", [])
    if all_answers:
        lines = []
        for i, ans in enumerate(all_answers):
            parts = []
            sel = ans.get("selected")
            if sel:
                # 前端始终返回数组（支持多选）
                if isinstance(sel, list) and len(sel) > 0:
                    parts.append(f"选择了: {', '.join(sel)}")
                elif isinstance(sel, str) and sel:
                    parts.append(f"选择了: {sel}")
            if ans.get("text"):
                parts.append(f"输入了: {ans['text']}")
            if parts:
                prefix = f"第{i + 1}页: " if len(all_answers) > 1 else ""
                lines.append(prefix + "；".join(parts))
        return "\n".join(lines) if lines else "用户已确认"

    return "用户已确认"
