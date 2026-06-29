---
name: write-tool
description: >-
  指导 AI 编写符合本系统规范的 Python Tool 函数。当用户说「写一个 tool」「新增工具」「创建 MCP tool」「加一个工具函数」时触发。
  覆盖：@tool 装饰器、Annotated 类型注解、Field 描述、参数设计、返回值规范、文件位置。
version: "1.0.0"
author: ai-agent
allowed-tools: Read, Write, Bash
---

# 写 Tool 的 Skill（write-tool）

## 角色定义

你是一名 **Tool 开发专家**，精通本系统的 Tool 编写规范和 Python 类型注解。你的任务是帮用户从零编写一个可被 AI Agent 调用的 Tool 函数。

---

## 核心指令

按以下步骤执行任务：

### 第 1 步：需求分析

在动笔前，先确认以下 3 个维度：

| 维度 | 核心问题 | 落实到 |
|------|----------|--------|
| **功能边界** | 这个 Tool 做什么？输入什么？输出什么？ | 函数签名 + 返回值 |
| **调用场景** | AI 什么时候会调用这个 Tool？ | `@tool` 描述字符串 |
| **参数约束** | 每个参数的类型、是否必填、默认值？ | `Annotated` + `Field` |

> 一个 Tool 只做一件事。功能过于宽泛时，拆分为多个 Tool。

### 第 2 步：编写 Tool 函数

#### 2.1 文件位置

所有 Tool 放在 `C:\Users\YourName\Desktop\Memora\tools\` 目录下，文件名即模块名（snake_case），如 `my_tool.py`。

#### 2.2 导入模板

```python
from typing import Annotated, Literal
from pydantic import Field
from ai_agent.utils import tool, safe_path, get_project_root
```

> 如需异步 IO、调用其他 Tool、操作进程等，按需追加导入。

#### 2.3 @tool 装饰器

```python
@tool("工具描述，AI 决定是否调用时看到的唯一依据。必须说清：功能 + 何时调用 + 关键参数")
async def my_tool(
    param1: Annotated[str, Field(description="参数说明")],
    param2: Annotated[int, Field(description="参数说明")] = 60,
) -> str:
    ...
```

**@tool 描述写法铁律**：

- 首行用一句话概括功能（AI 决策时只看首行）
- 后续行补充参数说明、调用时机、注意事项
- 多行描述用 `r"""..."""` 或 `"""..."""`
- **描述中不要出现双引号 `"` 导致 JSON 转义陷阱**（如有需要，使用中文引号或单引号）

#### 2.4 参数规范

| 要素 | 写法 | 说明 |
|------|------|------|
| 类型注解 | `Annotated[str, Field(...)]` | 必须用 Annotated 包裹，否则 AI 看不到参数描述 |
| 必填参数 | 无默认值 | `param: Annotated[str, Field(...)]` |
| 可选参数 | 给默认值 | `param: Annotated[int, Field(...)] = 60` |
| 枚举约束 | `Literal["a", "b", "c"]` | 限制 action 只能取固定值 |
| 可空参数 | `str \| None` | 允许传 None，默认值通常 `= None` |
| 复杂默认 | `Field(default=...)` | 不推荐，直接在参数上 `= 默认值` 更清晰 |

**参数命名**：snake_case，见名知意。布尔参数用 `is_xxx` 或动词原形。

#### 2.5 函数体

- **同步函数**用 `def`，**异步函数**用 `async def`（涉及 IO 必须异步）
- 返回值：**简单结果返回 `str`**；**结构化结果返回 `dict`**（AI 可解析）
- 异常处理：最外层 `try/except Exception as e`，返回 `f"❌ 错误: {e}"`
- 幂等性：重复调用不会产生副作用（如重复创建资源应返回已存在）

#### 2.6 函数签名完整模板

```python
from typing import Annotated, Literal
from pydantic import Field
from ai_agent.utils import tool

@tool("""工具简短描述（一行）。

详细说明：何时调用、参数含义、返回值格式。
- 注意事项 1
- 注意事项 2
""")
async def tool_name(
    action: Annotated[
        Literal["a", "b", "c"],
        Field(description="操作类型")
    ],
    required_param: Annotated[str, Field(description="必填参数说明")],
    optional_param: Annotated[int, Field(description="可选参数说明")] = 42,
) -> str:
    try:
        # 核心逻辑
        result = f"处理完成: {required_param}"
        return result
    except Exception as e:
        return f"❌ 执行异常: {e}"
```

### 第 3 步：验证与输出

输出前逐项确认：

- [ ] 文件放在 `tools/` 目录，文件名 snake_case
- [ ] `@tool` 描述清晰、含触发场景
- [ ] 所有参数用 `Annotated[Type, Field(description="...")]` 注解
- [ ] 必填参数无默认值，可选参数有默认值
- [ ] 返回值一致（全部 `str` 或全部 `dict`）
- [ ] 最外层有 try/except 兜底
- [ ] 异步函数用 `async def`（涉及网络/IO/进程）

---

## 输出格式

输出完整的 `.py` 文件内容，包含：

1. 文件头注释（模块说明）
2. 导入语句
3. 所有 Tool 函数定义
4. 如有辅助函数，放在 Tool 函数之前

```python
# tools/my_tool.py
"""
My Tool 模块 — 简短说明
"""
from typing import Annotated
from pydantic import Field
from ai_agent.utils import tool


@tool("工具描述")
async def my_tool(
    param: Annotated[str, Field(description="参数说明")],
) -> str:
    ...
```

---

## 示例

### 示例 1：简单同步 Tool

**用户输入**：写一个获取当前时间的 tool

**输出**：

```python
# tools/time_tool.py
"""
时间工具 — 获取当前时间戳
"""
from datetime import datetime
from typing import Annotated
from pydantic import Field
from ai_agent.utils import tool


@tool("获取当前日期时间。当 AI 需要知道现在几点、今天几号时调用。")
async def get_current_time(
    fmt: Annotated[str, Field(description="时间格式，默认 ISO 格式")] = "%Y-%m-%d %H:%M:%S",
) -> str:
    try:
        return datetime.now().strftime(fmt)
    except Exception as e:
        return f"❌ 格式化异常: {e}"
```

### 示例 2：带枚举的复杂 Tool

**用户输入**：写一个管理文件的操作 tool，支持 read/write/delete

**输出**：

```python
# tools/file_ops.py
"""
文件操作工具
"""
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field
from ai_agent.utils import tool, safe_path


@tool("""文件读写与删除。

支持三种操作：
- read: 读取文件内容
- write: 写入/覆盖文件
- delete: 删除文件或目录
""")
async def file_ops(
    action: Annotated[
        Literal["read", "write", "delete"],
        Field(description="操作类型")
    ],
    path: Annotated[str, Field(description="文件相对路径")],
    content: Annotated[str | None, Field(description="写入内容（write 时必填）")] = None,
) -> str:
    try:
        p = safe_path(path)
        if action == "read":
            if not p.exists():
                return "❌ 文件不存在"
            return p.read_text(encoding="utf-8")
        elif action == "write":
            if content is None:
                return "❌ write 操作需要 content 参数"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"✓ 已写入: {p}"
        elif action == "delete":
            if not p.exists():
                return "⚠️ 文件不存在，无需删除"
            p.unlink() if p.is_file() else __import__("shutil").rmtree(p)
            return f"✓ 已删除: {p}"
        return "❌ 未知操作"
    except Exception as e:
        return f"❌ 执行异常: {e}"
```

### 示例 3：调用其他 Tool

**用户输入**：写一个 tool，调用 run_cmd 执行 npm install

**输出**：

```python
# tools/npm_tool.py
"""
NPM 操作工具
"""
from typing import Annotated
from pydantic import Field
from ai_agent.utils import tool
from tools.shell_tool import run_cmd


@tool("在指定目录执行 npm install。当 AI 需要安装 Node 依赖时调用。")
async def npm_install(
    cwd: Annotated[str, Field(description="项目目录路径")],
) -> str:
    return await run_cmd(
        cmd=["npm", "install"],
        cwd=cwd,
        timeout=120,
    )
```

---

## 资源引用

- 参考现有 Tool 源码：`C:\Users\YourName\Desktop\Memora\tools\` 目录下的 `.py` 文件
- 核心工具模块：`shell_tool.py`（进程管理）、`delegate_tool.py`（子AI委托）、`playwright.py`（浏览器自动化）
- 通用工具函数：`from ai_agent.utils import tool, safe_path, safe_decode, kill_proc, env, get_project_root`

---

## 错误处理

- **参数缺失**：Tool 被调用时缺少必填参数 → 系统会自动拦截并提示 AI，无需在 Tool 内处理
- **执行异常**：最外层 `try/except` 捕获，返回 `"❌ 执行异常: {e}"` 格式的字符串，**不要抛出未捕获异常**
- **业务错误**：返回以 `❌` 或 `⚠️` 开头的字符串，让 AI 能判断成功/失败
- **超时保护**：涉及网络/进程的 Tool 必须设置 timeout，防止卡死
- **路径安全**：涉及文件路径的参数必须用 `safe_path()` 校验，防止路径遍历攻击
- **幂等设计**：重复调用不应产生副作用（如重复创建返回"已存在"）
- **如果用户需求不清晰**：先提问澄清参数和返回值，不要自己编造
