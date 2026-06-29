"""
工具索引管理器 —— 从 shared/tool/ 和 local/tool/ 加载所有 Python 工具模块。

从 tool_registry.py 拆分出来，因为工具加载来源与工具注册/调用是独立职责。
ToolRegistry 只负责注册/调用，ToolIndexManager 负责多源加载。

对标 SkillIndexManager：
    SkillIndexManager → 扫描 shared/skill/ + local/skill/ 的 SKILL.md
    ToolIndexManager  → 导入 shared/tool/ + local/tool/ 的 Python 模块
"""
from ai_agent.tool_registry import ToolRegistry


class ToolIndexManager:
    """扫描 shared/tool/ 和 local/tool/，导入所有 Python 工具模块。

    每个 .py 文件中的 @tool 装饰器在 import 时自动注册到 ToolRegistry，
    因此只需导入模块即可，无需解析文件内容。

    所有方法为 static，不依赖任何实例状态。
    由 AppContainer.init() 调用，与 SkillIndexManager 并列。
    """

    @staticmethod
    def load_all(registry: ToolRegistry) -> None:
        """从 shared/tool/ 和 local/tool/ 导入所有工具模块。

        - shared/tool/ → 公有工具，随 git 分发
        - local/tool/  → 本机独有，不提交。整目录被 .gitignore 排除

        任一目录不存在时静默跳过（local/tool/ 可能尚未创建）。
        """
        for pkg in ("shared.tool", "local.tool"):
            try:
                registry.load_all_from_package(pkg)
            except ModuleNotFoundError:
                pass
