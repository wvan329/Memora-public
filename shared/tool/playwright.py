import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Annotated, Literal

_log = logging.getLogger(__name__)
from pydantic import Field

from ai_agent.permissions import browser_session_id
from ai_agent.platform_utils import resolve_win_node_cmd
from ai_agent.utils import tool
from ai_agent.settings import settings
from shared.tool.shell_tool import subprocess_exec


def _ensure_playwright_config() -> None:
    """确保 playwright-cli 配置文件存在，启用 file:// 协议访问。

    playwright-cli 底层和 @playwright/mcp 共用同一个 Playwright 引擎，
    checkUrlAllowed() 通过 config.allowUnrestrictedFileAccess 控制是否放行
    file:// URL。playwright-cli 未将此开关暴露为 CLI 参数，需通过配置文件传入。
    """
    config_dir = Path(settings.work_dir) / ".playwright"
    config_file = config_dir / "cli.config.json"
    if not config_file.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "allowUnrestrictedFileAccess": True,
            "browser": {
                "launchOptions": {
                    "args": ["--allow-file-access-from-files"]
                }
            }
        }
        config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════
# 跨平台 npx 基础命令解析
# ═══════════════════════════════════════════════════════════════
# Windows：绕过 npx.cmd → 直接用 node.exe + npx-cli.js，彻底避开
# cmd.exe 对 URL 中 & 的误解析。macOS/Linux 无此问题，保持 "npx"。
_WIN_NPX_CACHE: tuple[str, str] | None = None


def _resolve_win_npx() -> list[str] | None:
    """Windows：解析 npx.cmd → [node.exe, npx-cli.js]（带缓存）。

    委托 platform_utils.resolve_win_node_cmd 做实际解析，
    本层仅维护惰性校验缓存（nvm 切换版本后自动刷新）。
    """
    global _WIN_NPX_CACHE

    if _WIN_NPX_CACHE is not None:
        node_exe, npx_cli = _WIN_NPX_CACHE
        if Path(node_exe).is_file() and Path(npx_cli).is_file():
            return [node_exe, npx_cli]
        _WIN_NPX_CACHE = None

    result = resolve_win_node_cmd("npx.cmd")
    if result:
        _WIN_NPX_CACHE = (result[0], result[1])
    return result


def _get_npx_base_cmd() -> list[str]:
    """跨平台：返回 playwright-cli 之前的基础命令部分。"""
    if sys.platform == "win32":
        resolved = _resolve_win_npx()
        if resolved:
            return resolved  # ["D:/.../node.exe", "D:/.../npx-cli.js"]
        return ["npx.cmd"]   # 降级：保留原始行为（& 问题恢复）
    else:
        return ["npx"]


# ═══════════════════════════════════════════════════════════════
# eval / run-code 超时常量
# ═══════════════════════════════════════════════════════════════
# eval：page.evaluate 在浏览器侧执行，无网络等待链 → 10s 足够
# run-code：有 waitForCompletion 等待链（最多 16s）+ npx 冷启动 → 需 30s
DEFAULT_EVAL_TIMEOUT = 10
DEFAULT_RUNCODE_TIMEOUT = 30


def _get_daemon_base() -> Path:
    """返回 playwright daemon 基础目录（跨平台），不含子目录。

    Windows: %LOCALAPPDATA%/ms-playwright/daemon
    macOS:   ~/Library/Caches/ms-playwright/daemon
    Linux:   ~/.cache/ms-playwright/daemon
    """
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" / "daemon"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright" / "daemon"
    else:
        return Path.home() / ".cache" / "ms-playwright" / "daemon"


def _get_all_daemon_dirs() -> list[Path]:
    """返回所有 playwright daemon 子目录列表。

    一台机器上可能存在多个 playwright 实例（多次安装/不同版本），
    每个实例有自己的 daemon 子目录。遍历所有子目录确保能找到正确的 session 文件。
    """
    base = _get_daemon_base()
    if not base.exists():
        return []
    return sorted(
        [d for d in base.iterdir() if d.is_dir()],
        key=lambda p: p.stat().st_mtime, reverse=True  # 最近修改的优先
    )


def _get_daemon_dir() -> Path | None:
    """定位 playwright daemon 目录（跨平台）—— 返回最近使用的子目录。

    保留此函数以兼容 browser_session_manager 等其他调用方。
    """
    dirs = _get_all_daemon_dirs()
    return dirs[0] if dirs else None


async def _force_cleanup_browser(sid: str):
    """强制清理浏览器会话：发 close 命令 + 删除残留的 session/err 文件。

    close 命令以 settings.work_dir 为 CWD（与正常 playwright 命令一致），
    通过 -s={sid} 定位会话，不依赖 daemon 目录中的 session 文件。
    """
    session_name = sid or "default"

    # 发送 close 命令
    try:
        parts = _get_npx_base_cmd() + ["playwright-cli"]
        if sid:
            parts.append(f"-s={sid}")
        parts.append("close")
        await subprocess_exec(cmd=parts, cwd=settings.work_dir, timeout=10)
    except Exception as e:
        _log.warning(f"close 命令失败 (会话 {session_name}): {e}")

    # 删除所有 daemon 子目录中的残留 session/err 文件
    for daemon_dir in _get_all_daemon_dirs():
        (daemon_dir / f"{session_name}.session").unlink(missing_ok=True)
        (daemon_dir / f"{session_name}.err").unlink(missing_ok=True)



def _has_statement_keywords(js: str) -> bool:
    """检测 JS 代码是否包含语句级关键字（需要 IIFE 包装或走 run-code）。"""
    stmt_kw = r'\b(?:var|let|const|function|if|for|while|switch|try|class|do|with|throw|debugger)\b'
    return bool(re.search(stmt_kw, js))


def _wrap_statement_as_iife(js: str) -> str:
    """将含语句（let/const/if/for 等）的 JS 代码包装为 IIFE。

    目的：让这类代码能通过 eval 通道（page.evaluate）执行，
    完全绕过 run-code 的 waitForCompletion 等待链陷阱。

    转换示例：
        "let x = 1; return x + 2"
        → "(function() { let x = 1; return x + 2 })()"

        "let links = document.querySelectorAll('a'); links.length"
        → "(function() { let links = ...; return links.length })()"
    """
    code = js.strip()
    # 自动补 return：最后一行（或最后一个 ; 后的表达式）如果不是语句开头，当作表达式
    if not re.search(r'\breturn\b', code):
        lines = code.rstrip(';').split('\n')
        last = lines[-1].strip()
        # 单行无换行时，尝试按 ; 分割取最后一段
        if len(lines) == 1 and ';' in last:
            last = last.rsplit(';', 1)[-1].strip()
        stmt_like = (
            r'^\s*(?:var|let|const|if|for|while|switch|try|do'
            r'|throw|debugger|class|function|catch|finally|else'
            r'|//|/\*|[})])\b'
        )
        if not re.match(stmt_like, last) and not last.startswith('//'):
            if len(lines) == 1 and ';' in lines[0]:
                # 单行多语句：替换最后一个 ; 后的表达式
                before, after = lines[0].rsplit(';', 1)
                lines[0] = before + '; return ' + after.strip()
            else:
                lines[-1] = 'return ' + last
        code = '\n'.join(lines)
    # 安全单行化：复用 _safe_singleline —— 自动补分号 + 注释转换，
    # 避免粗暴 replace('\n', ' ') 导致无分号多行代码语法错误
    code = _safe_singleline(code)
    return f'(function() {{ {code} }})()'


def _find_comment_outside_literal(code: str) -> int | None:
    """定位第一个「不在字符串/正则/模板字面量内」的 //。

    跟踪四种字面量状态：'...'、"..."、`...`、/.../。
    返回 // 的位置索引，或 None。

    启发式实现，覆盖 99% 场景。局限：不处理转义引号嵌套、
    正则字符类中的 /。作为 CLI 压缩辅助函数可接受。
    """
    in_single = False
    in_double = False
    in_template = False
    in_regex = False
    i = 0
    n = len(code)

    while i < n:
        ch = code[i]

        # ── 字面量结束 ──
        if in_single:
            if ch == '\\' and i + 1 < n:
                i += 2; continue
            if ch == "'":
                in_single = False
            i += 1; continue
        if in_double:
            if ch == '\\' and i + 1 < n:
                i += 2; continue
            if ch == '"':
                in_double = False
            i += 1; continue
        if in_template:
            if ch == '\\' and i + 1 < n:
                i += 2; continue
            if ch == '`':
                in_template = False
            i += 1; continue
        if in_regex:
            if ch == '\\' and i + 1 < n:
                i += 2; continue
            if ch == '/':
                in_regex = False
            i += 1; continue

        # ── 字面量开始 ──
        if ch == "'":
            in_single = True; i += 1; continue
        if ch == '"':
            in_double = True; i += 1; continue
        if ch == '`':
            in_template = True; i += 1; continue

        # ── 正则检测 ──
        if ch == '/' and i + 1 < n:
            if code[i + 1] == '/':
                return i  # 找到行注释
            if code[i + 1] == '*':
                end = code.find('*/', i + 2)
                if end != -1:
                    i = end + 2; continue
                return None
            # 启发式：/ 前面是运算符/括号/逗号等 → 正则
            if i == 0:
                in_regex = True
            else:
                before = code[i - 1]
                if before in '=!&|?~,:;([{<>%^*+-' or before.isspace():
                    in_regex = True

        i += 1
    return None


def _safe_singleline(js: str) -> str:
    """安全地将多行 JS 转为 CLI 友好的单行。

    策略：
    1. 换行处补分号（防止 ASI 导致语法错误）
    2. // 行注释 → /* */ 块注释
    3. 折叠换行为空格（不压缩字符串内空白）

    相比原来的「正则删 // + 全量压缩空白」，此方案：
    - 不误删正则/字符串中的 //
    - 不破坏无分号代码的语法
    """
    if '\n' not in js and '\r' not in js:
        return js.strip()

    lines = js.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    processed = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            continue
        comment_at = _find_comment_outside_literal(stripped)
        if comment_at is not None:
            before = stripped[:comment_at].rstrip()
            after = stripped[comment_at + 2:]
            if before:
                if before[-1] not in '{};,:[(':
                    before += ';'
                processed.append(f'{before} /*{after}*/')
            else:
                processed.append(f'/*{after}*/')
        else:
            if stripped[-1] not in '{};,:[(':
                stripped += ';'
            processed.append(stripped)

    return ' '.join(processed).strip()


def _wrap_js_for_eval(js: str, has_ref: bool) -> str | None:
    """预处理 eval 的 js 代码。

    - 有 ref：简单箭头表达式，CLI 包装为 locator.evaluate('el => ...')
    - 无 ref + 简单表达式：直接传入，CLI 包装为 () => (expr)
    - 无 ref + 含语句关键字：返回 None，由调用方 IIFE 包装后走 page.evaluate

    Returns:
        处理后的 JS 字符串，或 None（表示需要 IIFE 包装）
    """
    if has_ref:
        code = js.strip()
        if not code:
            raise ValueError("eval 的 js 参数不能为空。")
        if '\n' in code or re.search(r'(?:=>|function)\s*[\{\(]', code):
            raise ValueError(
                "eval 带 ref 只支持简单箭头表达式（如 el => el.textContent、el => el.id），"
                "不支持 function 关键字或函数体 {{}}。复杂逻辑请改用 run-code。"
            )
        return re.sub(r'\s+', ' ', code).strip()

    code = js.strip()
    if not code:
        raise ValueError("eval 的 js 参数不能为空。")

    # 含语句关键字 → 返回 None，由调用方 IIFE 包装
    if _has_statement_keywords(code):
        return None

    # 简单表达式
    if '\n' in code:
        return re.sub(r'\s+', ' ', code.replace('\n', ' ').replace('\r', ' ')).strip()
    return code


async def _probe_page_alive(sid: str, timeout: int = 6) -> tuple[bool, str]:
    """通过 snapshot 命令试探页面是否存活。

    相比 eval/run-code，snapshot 不经过 JS 执行通道，
    直接走 CDP → 即使页面有持续网络活动也能正常返回。

    Returns:
        (alive, detail): alive=True 页面正常，detail 是 snapshot 结果
    """
    parts = _get_npx_base_cmd() + ["playwright-cli"]
    if sid:
        parts.append(f"-s={sid}")
    parts.append("snapshot")
    try:
        result = await subprocess_exec(cmd=parts, cwd=settings.work_dir, timeout=timeout)
    except Exception:
        return False, "snapshot 试探异常"
    result_str = str(result)
    if "命令超时" in result_str:
        return False, "snapshot 也超时"
    if "Error" in result_str or "error" in result_str[:100]:
        return False, result_str
    return True, result_str


async def _handle_wait_for(
    sid: str,
    selector: str | None,
    text: str | None,
    wait_state: str,
    timeout: int,
    work_dir: str,
) -> str:
    """处理 wait-for action。P0：selector/text → page.waitForSelector()。

    内部转为 run-code 调用 Playwright 的 waitForSelector API。
    P1 增强：成功后自动获取元素信息（text/tag/id/className/rect）。

    Returns:
        JSON 字符串：{"found": true/false, ...}
    """
    # 确定目标选择器
    if text:
        safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
        target_selector = f'text="{safe_text}"'
    elif selector:
        target_selector = selector.replace('\\', '\\\\').replace("'", "\\'")
    else:
        return json.dumps({
            "found": False,
            "error": "wait-for 需要至少指定 selector 或 text 参数"
        }, ensure_ascii=False)

    valid_states = {"visible", "hidden", "attached", "detached"}
    if wait_state not in valid_states:
        return json.dumps({
            "found": False,
            "error": f"无效的 wait_state: '{wait_state}'，有效值: {', '.join(sorted(valid_states))}"
        }, ensure_ascii=False)

    timeout_ms = timeout * 1000

    # 构造 run-code：等待 + 成功后获取元素信息
    js_code = (
        "async page => {"
        "  const __start = Date.now();"
        "  try {"
        "    await page.waitForSelector("
        f"      '{target_selector}',"
        f"      {{ state: '{wait_state}', timeout: {timeout_ms} }}"
        "    );"
        "    const __info = await page.$eval("
        f"      '{target_selector}',"
        "      function(el) {"
        "        var r = el.getBoundingClientRect();"
        "        return {"
        "          text: (el.textContent || '').trim().substring(0, 300),"
        "          tag: (el.tagName || '').toLowerCase(),"
        "          id: el.id || '',"
        "          className: (typeof el.className === 'string'"
        "            ? el.className : (el.getAttribute && el.getAttribute('class') || '')),"
        "          bounding_box: {"
        "            x: Math.round(r.x), y: Math.round(r.y),"
        "            width: Math.round(r.width), height: Math.round(r.height)"
        "          }"
        "        };"
        "      }"
        "    );"
        "    __info.found = true;"
        "    __info.elapsed_ms = Date.now() - __start;"
        "    return JSON.stringify(__info);"
        "  } catch(__e) {"
        "    var __msg = __e.message || String(__e);"
        "    return JSON.stringify({"
        "      found: false,"
        "      isTimeout: __msg.indexOf('Timeout') >= 0,"
        "      error: __msg.substring(0, 250),"
        "      elapsed_ms: Date.now() - __start"
        "    });"
        "  }"
        "}"
    )

    # 安全单行化
    js_code = _safe_singleline(js_code)

    parts = _get_npx_base_cmd() + ["playwright-cli"]
    if sid:
        parts.append(f"-s={sid}")
    parts.extend(["run-code", js_code])

    result = await subprocess_exec(
        cmd=parts,
        cwd=work_dir,
        timeout=timeout + 5,
    )

    # 尝试从结果中提取 JSON
    json_match = re.search(r'\{.*\}', str(result), re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return str(result)


@tool("""调用 Playwright CLI 执行浏览器操作。

每次命令执行后返回页面状态快照，包含可交互元素的 ref（如 e15）。
后续用 ref 来精确点击、填入等。ref 也可用 CSS 选择器或 Playwright locator。

## 参数
- action: 命令名。open 只能调一次！
- ref:    元素引用（e15）、CSS 选择器（#id）或 locator（getByRole(...)）
- text:   输入/填入文本、drag 终点 ref、upload 文件路径、dialog-accept 提示
- url:    网页地址（open/goto/tab-new）
- js:     eval / run-code 代码
- key:    按键名（Enter / ArrowLeft / a / Shift / Escape / Tab 等）
- value:  选项值（select）
- width/height: 像素（resize）
- button: left/right/middle（click/dblclick，默认 left）
- full_page: screenshot 是否全页长图
- submit: fill 后是否回车

## eval — 浏览器侧执行，直接访问 document
- 无 ref：简单表达式，CLI 包装为 `() => (expr)`。
  例：document.title、document.querySelector('#id').value、document.querySelectorAll('a').length
- 有 ref：简单箭头表达式（不能有 {} 函数体），接收元素引用。
  例：el => el.textContent、el => el.id、el => el.getAttribute('href')
- ❌ 不支持任何语句（if/for/let/const/var/return 等），不支持函数体 {}

## run-code — Node.js 侧执行，传入 Playwright Page 对象
- 必须用函数形式：async page => { ... } 或 async (page) => { ... }
  也可以简写（自动补全）：page => page.title()、return await page.title();
- ❌ 不能直接访问 document！必须通过 page.evaluate(() => document.xxx)
- ❌ 禁止使用 // 单行注释（会被压缩破坏语法），请用 /* */ 块注释
- ✅ 所有 Playwright API 可用：page.goto()、page.click()、page.evaluate()、
  page.locator()、page.$$eval()、page.waitForSelector()、page.emulateMedia() 等
- 返回值用 return，会显示在结果中

## wait-for — 等待页面元素进入指定状态（SPA 页面必备）
- selector: CSS 选择器（如 '.modal'、'#result'）
- text: 页面文本片段（内部转为 text= 选择器，如 '加载完成'）
- wait_state: 等待状态 — visible（默认）/ hidden / attached / detached
- timeout: 超时秒数（默认 60），内部转为毫秒传给 page.waitForSelector
- 返回值：{"found": true/false, "text": "...", "tag": "div", "id": "...", "elapsed_ms": 2100}
- 典型用法：goto SPA → wait-for selector=".content" → snapshot → 继续操作
""")
#
async def playwright(
        action: Annotated[
            Literal[
                "open", "goto", "close-all", "kill-all", "close",
                "snapshot", "click", "dblclick", "type", "fill",
                "screenshot", "pdf", "eval",
                "press", "keydown", "keyup",
                "select", "hover", "drag", "drop",
                "check", "uncheck", "resize",
                "go-back", "go-forward", "reload",
                "tab-list", "tab-new", "tab-close", "tab-select",
                "run-code",
                "dialog-accept", "dialog-dismiss",
                "upload",
                "list",
                "wait-for",
            ],
            Field(description="命令名称")
        ],

        # ── 核心交互参数 ──
        ref: Annotated[str | None, Field(description="元素引用（如 e15），用于 click/dblclick/hover/"
                                                     "select/check/uncheck/fill/drag/drop/"
                                                     "snapshot/screenshot/eval/"
                                                     "tab-select/tab-close")] = None,
        text: Annotated[str | None, Field(description="文本内容。用于 type（输入的文字）、fill（填入的文字）、"
                                                      "drag（终点ref）、upload（文件路径）、"
                                                      "dialog-accept（提示文本）")] = None,
        url: Annotated[str | None, Field(description="网页 URL。用于 open、goto、tab-new")] = None,
        js: Annotated[str | None, Field(description="JavaScript 代码。用于 eval、run-code。"
                                                    "eval 仅支持简单表达式（如 'document.title'）；"
                                                    "run-code 需传函数（如 'async page => { ... }'）。"
                                                    "⚠️ 不要包含中文注释，建议压缩为单行。")] = None,
        key: Annotated[str | None, Field(description="按键名（Enter/ArrowLeft/ArrowDown/Tab/Escape/a/Shift 等）。"
                                                     "用于 press、keydown、keyup")] = None,

        # ── 输出文件 ──
        filename: Annotated[str | None, Field(description="输出文件名。用于 screenshot/pdf/snapshot/run-code")] = None,

        # ── 数值/值参数 ──
        value: Annotated[str | None, Field(description="选项值。用于 select")] = None,
        width: Annotated[int | None, Field(description="宽度（像素），resize 用")] = None,
        height: Annotated[int | None, Field(description="高度（像素），resize 用")] = None,
        button: Annotated[str | None, Field(description="鼠标按钮（left/right/middle）。"
                                                        "用于click/dblclick 可选，默认 left")] = None,
        full_page: Annotated[
            bool | None, Field(description="screenshot用，是否截完整页面长图（包括滚动区域），默认False")] = False,
        submit: Annotated[bool | None, Field(description="fill用，fill后是否直接按enter，默认False")] = False,
        timeout: Annotated[int, Field(description="超时秒数，默认 60")] = 60,
        wait_state: Annotated[
            str | None,
            Field(description="wait-for 等待的状态：visible（默认）/ hidden / attached / detached")
        ] = None,
):
    # ── session 处理 ──
    # browser_session_id 由 browser_task 显式设置，不受 ConversationTurn 覆盖影响。
    # 默认值 "__unset__" 表示未被 browser_task 设置 → 主 AI 直接调用 → 默认浏览器。
    # （delegate 已被 deny playwright，browser_inner 走 browser_task 管线，无其他角色能走到这里）
    bsid = browser_session_id.get()
    if bsid != "__unset__":
        sid = bsid  # browser_task 已设：uuid=隔离浏览器
    else:
        sid = ""   # 主 AI 直接调用 playwright → 默认浏览器

    parts = _get_npx_base_cmd() + ["playwright-cli"]
    if sid:
        parts.append(f"-s={sid}")

    act: str = action.strip()
    if sid:
        # 子 AI 没有其他浏览器的权限，kill-all/close-all 降级为 close 自己
        if act in ["kill-all", "close-all"]:
            act = "close"
    parts.append(act)

    # ── 按命令拼装参数 ──
    # url 类
    if act in ("open", "goto", "tab-new") and url:
        parts.append(url)

    # ref 类（大多数交互命令）
    if act in ("click", "dblclick", "hover", "check", "uncheck",
               "select", "fill", "drag", "drop",
               "tab-select", "tab-close"):
        if ref:
            parts.append(ref)

    # snapshot / screenshot 可选 ref
    if act in ("snapshot", "screenshot") and ref:
        parts.append(ref)

    # text 类
    if act in ("type", "upload", "dialog-accept") and text:
        parts.append(text)

    # fill 第二个参数
    if act == "fill" and text:
        parts.append(text)

    # drag 第二个参数（终点 ref，用 text 传）
    if act == "drag" and text:
        parts.append(text)

    # js 类
    if act in ("eval", "run-code") and js is not None:
        js_code = js.strip()
        if act == "eval":
            # eval：优先尝试简单表达式，含语句关键字时自动 IIFE 包装
            try:
                eval_code = _wrap_js_for_eval(js_code, has_ref=bool(ref))
            except ValueError as e:
                return str(e)
            if eval_code is None:
                # 含语句关键字 → IIFE 包装，走 page.evaluate 在浏览器侧执行
                # 完全绕过 run-code 的 waitForCompletion 等待链
                if ref:
                    return (
                        "eval 带 ref 只支持简单箭头表达式。"
                        "复杂逻辑（含 let/const/if/for 等）请改用 run-code：\n"
                        "  run-code, js='async page => { ... }'"
                    )
                try:
                    js_code = _wrap_statement_as_iife(js_code)
                except Exception as e:
                    return f"eval 代码 IIFE 包装失败: {e}"
            else:
                js_code = eval_code
            # eval 不压缩——CLI 对 eval 参数宽容，保留原始格式

        if act == "run-code":
            is_fn = bool(re.match(
                r'^\s*(?:async\s+function\s*\(|function\s*\(|'
                r'async\s*\(\s*\w*\s*\)\s*=>|'
                r'async\s+\w+\s*=>|'
                r'\(\s*\w*\s*\)\s*=>|'
                r'\w+\s*=>)',
                js_code))
            if not is_fn:
                js_code = f'async page => {{ {js_code} }}'
            elif re.match(r'^\s*(?:async\s+)?function\s*\(', js_code):
                js_code = f'({js_code})'
            else:
                if not js_code.strip().startswith('async') and re.search(r'\bawait\b', js_code):
                    js_code = 'async ' + js_code

            # 安全单行化：替代原来的正则删 // 注释 + 全量压缩空白
            # 避免误删正则/字符串中的 //，避免破坏无分号代码
            js_code = _safe_singleline(js_code)
        parts.append(js_code)

    # eval 的 ref 必须在 expression 之后（CLI 语法：eval <expression> [ref]）
    if act == "eval" and ref:
        parts.append(ref)

    # key 类
    if act in ("press", "keydown", "keyup") and key:
        parts.append(key)

    # value 类
    if act == "select" and value:
        parts.append(value)

    # resize
    if act == "resize":
        if width is not None:
            parts.append(str(width))
        if height is not None:
            parts.append(str(height))

    # button
    if act in ("click", "dblclick") and button:
        parts.append(button)

    if act.startswith('open'):
        # 确保 playwright-cli 配置文件存在（启用 file:// 协议）
        _ensure_playwright_config()
        # 先把浏览器关了 不然如果有没清理的会新建实例
        if not sid:  # 默认浏览器才持久化,不然太多了
            l = await playwright(action="list")
            if "- default:" in l:
                return "浏览器已打开 请勿重复打开"
            # 浏览器持久化目录见 _get_daemon_dir()
            parts.append('--persistent')
        if settings.browser_headed:
            parts.append("--headed")

    # ── filename 标志 ──
    if filename:
        parts.append(f"--filename={filename}")
    # ── extra 原始参数 ──
    if act == "screenshot" and full_page:
        parts.append("--full-page")
    if act == "fill" and submit:
        parts.append("--submit")
    # ── 超时策略 ──
    # eval：page.evaluate 在浏览器侧执行，无网络等待链 → 10s 足够
    # run-code：有 waitForCompletion 等待链（最多 16s）+ npx 冷启动 → 需 30s
    if act == "eval":
        timeout = min(timeout, DEFAULT_EVAL_TIMEOUT)
    elif act == "run-code":
        timeout = min(timeout, DEFAULT_RUNCODE_TIMEOUT)

    # ── wait-for 特殊处理 ──
    if act == "wait-for":
        return await _handle_wait_for(
            sid=sid,
            selector=ref,
            text=text,
            wait_state=wait_state or "visible",
            timeout=timeout,
            work_dir=settings.work_dir,
        )

    result = await subprocess_exec(
        cmd=parts,
        cwd=settings.work_dir,
        timeout=timeout,
    )

    # ── eval / run-code 超时诊断 ──
    # 不再一刀切返回「不支持」，而是 snapshot 试探 + 区分原因
    if act in ("eval", "run-code") and "命令超时" in str(result):
        probe_alive, probe_detail = await _probe_page_alive(sid, timeout=6)
        if not probe_alive and "也超时" in probe_detail:
            result = (
                "⚠️ 浏览器无响应 — eval/run-code 超时且 snapshot 试探也超时。\n"
                "浏览器可能已崩溃或卡死。建议 close 后重新 open 页面。"
            )
        elif not probe_alive:
            result = (
                "⚠️ 当前页面不支持 eval/run-code（可能是 text/plain 等非 HTML 页面）。\n"
                "请改用 snapshot 或 screenshot 获取页面内容。"
            )
        elif act == "eval":
            result = (
                f"⚠️ eval 在 {timeout}s 内未完成。可能原因：npx 冷启动（需 3-4s）。\n"
                "建议将 timeout 增加到 15s 后重试一次。如仍超时，请改用 snapshot。"
            )
        else:
            result = (
                f"⚠️ run-code 在 {timeout}s 内未完成 — 页面可能有持续网络活动。\n"
                "\n"
                "常见原因：\n"
                "① 页面有 XHR 轮询（如实时看板、消息推送）\n"
                "② WebSocket 心跳连接\n"
                "③ 慢速资源加载（大图片/视频）\n"
                "④ run-code 中 waitForSelector 等待不存在的元素\n"
                "\n"
                "解决建议：\n"
                "1. 如果只需读取 DOM 数据 → 改用 eval（eval 在浏览器侧执行，不受网络等待影响）\n"
                "2. 在 run-code 函数内显式设置超时：\n"
                "   await page.waitForLoadState('networkidle', { timeout: 5000 });\n"
                "3. 增大 timeout 参数后重试"
            )

    # close / close-all / kill-all 后清理残留文件。
    # npx playwright-cli 不会自动删除 .session 和 .err，需手动清理。
    if act in ("close", "close-all", "kill-all"):
        daemon_dir = _get_daemon_dir()
        if daemon_dir:
            if act == "close":
                session_name = sid or "default"
                (daemon_dir / f"{session_name}.session").unlink(missing_ok=True)
                (daemon_dir / f"{session_name}.err").unlink(missing_ok=True)
            else:
                # close-all / kill-all：npx 已关掉所有浏览器，全清（但保留 default）
                for f in daemon_dir.glob("*.session"):
                    if f.stem == "default":
                        continue
                    f.unlink(missing_ok=True)
                for f in daemon_dir.glob("*.err"):
                    f.unlink(missing_ok=True)

    return result
