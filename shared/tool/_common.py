"""
共享工具模块：被多个 tool 文件复用的基础定义。

包含：
- DelegateTask: 委托子任务的 Pydantic 模型
- _atomic_write: 原子文件写入工具函数
"""

from pathlib import Path

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════
# 共享数据模型
# ═══════════════════════════════════════════════════════

class DelegateTask(BaseModel):
    """单个子任务"""
    task: str = Field(description="子任务描述，越具体越好。子Agent会完整执行，所以必须描述清楚期望的产出")
    session_uuid: str = Field(
        default="",
        description="会话UUID。空字符串则自动新建并在任务完成后返回；传入已有UUID则对应的子ai继续对话。"
                    "⚠️ 多个并行任务必须使用不同的 session_uuid（各自独立），复用历史上下文时只能串行（n=1）！"
    )


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> str | None:
    """原子写入：先写 .tmp → 回读校验 → rename 到目标路径。

    调用方负责加锁（如已持有 RLock，此函数不重复加锁）。

    Returns:
        None 表示写入成功。
        str 表示错误信息（以 ❌ 开头），调用方可直接 return 或二次包装。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding=encoding)
    except PermissionError:
        return f"❌ 写入失败：文件被占用或无权限: {path}"
    except OSError as e:
        return f"❌ 写入失败：{e}"
    if tmp.read_text(encoding=encoding) != content:
        tmp.unlink(missing_ok=True)
        return f"❌ 写入验证失败：内容不一致（期望 {len(content)} 字符）"
    tmp.replace(path)
    return None
