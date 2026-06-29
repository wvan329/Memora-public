# tools/random_tool.py
"""
随机数工具 — 生成随机数、掷骰子、概率判定、随机选择。

适用于游戏（人生模拟器、跑团等）、需要随机决策的场景。
替代临时 Python 脚本生成随机结果的做法。
"""
import random
import re
from typing import Annotated, Literal
from pydantic import Field
from ai_agent.utils import tool


@tool("""随机数生成器，支持多种随机操作。

操作类型（action）：
- random_int: 生成 [min, max] 范围内的随机整数
- random_float: 生成 [min, max) 范围内的随机浮点数
- dice: 掷骰子，格式如 '2d6+3'（2个6面骰+3修正）
- prob_check: 概率判定，给定百分比概率，返回成功/失败
- random_choice: 从列表中随机选一项（choices: list[str]）
- coin: 抛硬币，返回正面/反面

适用场景：游戏随机事件、人生模拟器分支、需要随机决策的任何情况。
""")
async def random_tool(
    action: Annotated[
        Literal["random_int", "random_float", "dice", "prob_check", "random_choice", "coin"],
        Field(description="随机操作类型")
    ],
    min_val: Annotated[int | None, Field(description="最小值（random_int/random_float 用）")] = None,
    max_val: Annotated[int | None, Field(description="最大值（random_int/random_float 用）")] = None,
    dice_expr: Annotated[str | None, Field(description="骰子表达式，如 '2d6+3'（dice 用）")] = None,
    prob: Annotated[float | None, Field(description="成功概率百分比 0-100（prob_check 用）")] = None,
    choices: Annotated[list[str] | None, Field(description="选项列表，如 ['成功', '失败']（random_choice 用）")] = None,
) -> str:
    try:
        if action == "random_int":
            if min_val is None or max_val is None:
                return "❌ random_int 需要 min_val 和 max_val 参数"
            result = random.randint(min_val, max_val)
            return f"🎲 随机整数 [{min_val}, {max_val}]: {result}"

        elif action == "random_float":
            if min_val is None or max_val is None:
                return "❌ random_float 需要 min_val 和 max_val 参数"
            result = random.uniform(min_val, max_val)
            return f"🎲 随机浮点数 [{min_val}, {max_val}): {result:.4f}"

        elif action == "dice":
            if not dice_expr:
                return "❌ dice 需要 dice_expr 参数，如 '2d6+3'"
            # 解析骰子表达式: NdM+K 或 NdM-K 或 dM+K
            pattern = r'^(\d+)?d(\d+)([+-]\d+)?$'
            m = re.match(pattern, dice_expr.strip())
            if not m:
                return f"❌ 骰子表达式格式错误: '{dice_expr}'，正确格式如 '2d6+3'、'd20'、'3d8-1'"
            count = int(m.group(1)) if m.group(1) else 1
            sides = int(m.group(2))
            modifier = int(m.group(3)) if m.group(3) else 0

            if count < 1 or sides < 2:
                return f"❌ 骰子参数无效: count={count}, sides={sides}"
            if count > 100:
                return f"❌ 一次最多掷 100 个骰子"

            rolls = [random.randint(1, sides) for _ in range(count)]
            total = sum(rolls) + modifier
            detail = " + ".join(str(r) for r in rolls)
            if modifier != 0:
                detail += f" {'+' if modifier > 0 else '-'} {abs(modifier)}"
            return f"🎲 {dice_expr}: [{detail}] = {total}"

        elif action == "prob_check":
            if prob is None:
                return "❌ prob_check 需要 prob 参数（0-100）"
            if not (0 <= prob <= 100):
                return f"❌ 概率必须在 0-100 之间，当前为 {prob}"
            roll = random.random() * 100
            success = roll < prob
            return f"🎲 概率判定 ({prob}%): {'✅ 成功' if success else '❌ 失败'} (掷出 {roll:.1f})"

        elif action == "random_choice":
            if not choices:
                return "❌ random_choice 需要非空 choices 列表参数"
            result = random.choice(choices)
            return f"🎲 随机选择 ({len(choices)} 项): {result}"

        elif action == "coin":
            result = "正面" if random.random() < 0.5 else "反面"
            return f"🪙 抛硬币: {result}"

        return f"❌ 未知操作: {action}"

    except Exception as e:
        return f"❌ 随机工具异常: {e}"
