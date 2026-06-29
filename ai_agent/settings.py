import sys
import json

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import re


def inject_env(obj):
    pattern = re.compile(r"\{(\w+)\}")
    if isinstance(obj, dict):
        return {k: inject_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [inject_env(v) for v in obj]
    if isinstance(obj, str):
        def replace(match):
            key = match.group(1)
            return getattr(settings, key.lower(), match.group(0))

        return pattern.sub(replace, obj)
    return obj


def find_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / ".env").exists():
            return p
    raise RuntimeError("没找到项目根目录")


def _load_prompt(filename: str) -> str:
    """从项目根目录加载提示词文件，不存在则返回空字符串。"""
    f = Path(filename)
    return f.read_text(encoding="utf-8") if f.exists() else ""


ROOT = find_root(Path(__file__).resolve())


class Settings(BaseSettings):
    work_dir: str = ""
    shared_dir: str = ""
    black_dir: list[str] = []
    api_key: str = ""
    ali_api_key: str = ""
    ali_bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ali_vision_model: str = "qwen3.7-plus"
    model_name: str = ""
    # flash 模型名，用于翻译、welcome 等轻量高频任务（可配置以切换不同轻量模型）
    flash_model_name: str = "deepseek-v4-flash"
    base_url: str = ""
    system_prompt: str = ""           # 主 AI 提示词
    system_prompt_delegate: str = ""   # 委派子 AI 提示词
    system_prompt_browser: str = ""    # 浏览器子 AI 提示词
    db_path: str = "ai_chat.db"
    access_password: str = ""
    # 邮件通知配置
    email_smtp_server: str = ""
    email_smtp_port: int = 465
    email_from: str = ""
    email_password: str = ""
    email_to: str = ""
    # SSH 远程服务器
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_password: str = ""
    # 通知模式：开启后 AI 完成回复时推送手机通知
    notification_enabled: bool = False
    vision_high_res_default: bool = False  # 图片识别高精度默认值（对应.env的VISION_HIGH_RES_DEFAULT）
    browser_headed: bool = False  # 浏览器是否显示窗口（对应.env的BROWSER_HEADED）
    # 阿里云 ECS API
    ali_access_key_id: str = ""
    ali_access_key_secret: str = ""
    ali_ecs_region: str = "cn-beijing"
    ali_ecs_instance_id: str = ""
    # 高德地图 API Key（用于 maps_* 系列工具）
    amap_api_key: str = ""
    model_config = SettingsConfigDict(env_file=ROOT / ".env")


settings = Settings()

# 相对路径自动转为基于项目根目录的绝对路径
if settings.shared_dir and not Path(settings.shared_dir).is_absolute():
    settings.shared_dir = str(ROOT / settings.shared_dir)


def set_env_bool(key: str, value: bool) -> None:
    """将布尔型配置写入 .env 文件，key 不存在则追加。"""
    env_path = ROOT / ".env"
    env_text = env_path.read_text(encoding="utf-8")
    new_line = f"{key}={'true' if value else 'false'}"
    if re.search(rf'^{re.escape(key)}\s*=', env_text, re.MULTILINE | re.IGNORECASE):
        env_text = re.sub(
            rf'^{re.escape(key)}\s*=.*$', new_line, env_text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    else:
        env_text = env_text.rstrip() + "\n" + new_line + "\n"
    env_path.write_text(env_text, encoding="utf-8")


def _load_mcp_config():
    """从 mcp.json 加载 MCP 配置，转换为内部统一格式（三个数组）。"""
    config_path = ROOT / "mcp.json"
    if not config_path.exists():
        return {"stdio_list": [], "streamable_list": [], "sse_list": []}

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    result = {"stdio_list": [], "streamable_list": [], "sse_list": []}

    for _name, cfg in raw.get("mcpServers", {}).items():
        # 跳过显式禁用的
        if cfg.get("enabled") is False:
            continue

        # 提取 white/black（工具过滤）
        entry = {}
        if "white" in cfg:
            entry["white"] = cfg["white"]
        if "black" in cfg:
            entry["black"] = cfg["black"]

        # 传输类型必须显式指定，http 作为 streamable 的别名
        _TYPE_ALIAS = {"http": "streamable"}
        mcp_type = cfg.get("type", "")
        mcp_type = _TYPE_ALIAS.get(mcp_type, mcp_type)
        if mcp_type not in ("stdio", "streamable", "sse"):
            raise Exception(
                f"MCP 配置项 \"{_name}\" 必须指定 \"type\" 字段，"
                f"可选值: stdio / streamable(http) / sse，当前值: {mcp_type!r}"
            )

        if mcp_type == "stdio":
            entry["command"] = cfg["command"]
            entry["args"] = cfg.get("args", [])
            if "env" in cfg:
                entry["env"] = cfg["env"]
            result["stdio_list"].append(entry)
        elif mcp_type == "sse":
            entry["url"] = cfg["url"]
            if "headers" in cfg:
                entry["headers"] = cfg["headers"]
            result["sse_list"].append(entry)
        else:  # streamable（默认）
            entry["url"] = cfg["url"]
            if "headers" in cfg:
                entry["headers"] = cfg["headers"]
            result["streamable_list"].append(entry)

    return inject_env(result)


mcp_config = _load_mcp_config()
