"""
技能索引管理器 —— 从 SKILL.md 自动生成 INDEX.md + 加载各角色系统提示词。

从 tool_registry.py 拆分出来，因为技能索引生成与工具注册是独立职责。
ToolRegistry 只负责工具注册/调用，SkillIndexManager 负责技能文档和系统提示词。
"""
import re
from pathlib import Path

import yaml

from ai_agent.settings import ROOT, _load_prompt


class SkillIndexManager:
    """扫描 shared/skill/*/SKILL.md 的 YAML frontmatter，生成索引 + 加载角色提示词。

    所有方法为 static，不依赖任何实例状态。
    由 AppContainer.init() 调用，替代原来散落在 ToolRegistry 中的同名方法。
    """

    @staticmethod
    def build_index(shared_dir: str) -> None:
        """扫描 shared/skill/ 和 local/skill/ 的 SKILL.md，生成 INDEX.md。

        - shared/skill/ → 公有技能，随 git 分发
        - local/skill/  → 本机独有，不提交。整目录被 .gitignore 排除

        每个 SKILL.md 头部须含：
            ---
            name: skill-name
            description: 简短描述
            ---
        """
        skill_dir = Path(shared_dir) / "skill"
        local_dir = Path(ROOT) / "local" / "skill"

        rows: list[str] = []
        source_dirs = []
        if skill_dir.exists():
            source_dirs.append((skill_dir, "公有"))
        if local_dir.exists():
            source_dirs.append((local_dir, "本机"))

        if not source_dirs:
            return

        all_skill_mds = []
        for sd, scope in source_dirs:
            for skill_md in sorted(sd.glob("*/SKILL.md")):
                all_skill_mds.append((skill_md, scope))

        for skill_md, scope in all_skill_mds:
            try:
                text = skill_md.read_text(encoding="utf-8")
            except Exception:
                continue

            # 正则提取 ---...--- YAML frontmatter 块，用 yaml.safe_load 解析
            match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if not match:
                continue

            front = match.group(1)
            try:
                meta = yaml.safe_load(front)
            except yaml.YAMLError:
                continue

            if not isinstance(meta, dict):
                continue

            name = str(meta.get("name", "")).strip()
            desc = str(meta.get("description", "")).strip()

            if not name:
                continue

            rows.append(f"| {name} | {scope} | {desc} | `{skill_md}` |")

        if not rows:
            return

        index_md = f"""# Memora 技能索引

> **自动生成** — 每次启动程序时从各 `SKILL.md` 头部扫描生成。
> **使用方法**：先阅此索引快速定位需要的技能，再打开对应 `SKILL.md` 获取详细操作指南。

| 技能名 | 来源 | 触发场景 / 核心能力 | 文件 |
|--------|---------------------|------|
{chr(10).join(rows)}

## AI 使用约定

- **先读 INDEX.md** → 判断任务匹配哪个技能
- **再读对应 SKILL.md** → 获取详细操作指南
- **cmd-execution 是基础设施技能**：涉及任何命令执行、文件读写、进程操作时都必须先读它
"""
        (skill_dir / "INDEX.md").write_text(index_md, encoding="utf-8")

    @staticmethod
    def load_all_prompts(settings_obj) -> None:
        """加载所有角色的系统提示词到 settings_obj。

        在 container.init() 中调用，替代 ToolRegistry.load_all_system_prompts()。

        - 主 AI：system_prompt.txt + 技能索引
        - 委派子 AI：system_prompt_delegate.txt + 技能索引
        - 浏览器子 AI：system_prompt_browser.txt（无技能索引）
        - compress 角色：不使用系统提示词
        """
        import platform

        shared_dir = settings_obj.shared_dir
        SkillIndexManager.build_index(shared_dir)

        skill_index = (
            "你拥有的skill列表："
            + _load_prompt(str(Path(shared_dir) / "skill" / "INDEX.md"))
        )

        # 机器环境信息，注入到 system prompt 的 {env_info} 占位符
        env_info = (
            f"当前运行环境：{platform.system()} {platform.release()}，"
            f"主机名 {platform.node()}，Python {platform.python_version()}，"
            f"工作目录 {settings_obj.work_dir}，"
            f"项目源码 {ROOT}"
        )

        # 主 AI：完整提示词 + 技能索引
        raw_prompt = _load_prompt(str(ROOT / "system_prompt.txt"))
        settings_obj.system_prompt = (
            raw_prompt.replace("{env_info}", env_info)
                      .replace("{project_root}", str(ROOT))
            + skill_index
        )

        # 委派子 AI：精简提示词 + 技能索引（无此文件则回退到主提示词）
        delegate_prompt = _load_prompt(str(ROOT / "system_prompt_delegate.txt"))
        settings_obj.system_prompt_delegate = (
            (delegate_prompt + skill_index) if delegate_prompt else settings_obj.system_prompt
        )

        # 浏览器子 AI：极简提示词，无需技能索引
        browser_prompt = _load_prompt(str(ROOT / "system_prompt_browser.txt"))
        settings_obj.system_prompt_browser = browser_prompt or settings_obj.system_prompt
