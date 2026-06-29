from typing import Annotated, Literal, Optional, Union
import os
import re
import shutil
import datetime
import threading
from pathlib import Path
from difflib import unified_diff, SequenceMatcher

from pydantic import Field

from shared.tool._common import _atomic_write
from ai_agent.utils import tool, get_path
from ai_agent.settings import settings

# ── 文件级互斥锁：防止并发修改同一文件导致 .tmp 争抢（WinError 32）──
_file_locks: dict[str, threading.RLock] = {}
_file_locks_guard = threading.Lock()

def _get_file_lock(path: str) -> threading.RLock:
    """获取指定文件的互斥锁，确保同一文件同一时间只有一个写操作。"""
    key = os.path.normpath(str(path)).lower()
    with _file_locks_guard:
        lock = _file_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _file_locks[key] = lock
        return lock

# 检测正则特殊字符，用于给 AI 返回智能提示
_REGEX_CHARS = re.compile(r'[|*.\\+?(){}\[\]^$]')

def _trunc(s: str, n: int = 60) -> str:
    """截断字符串，超出长度加 ..."""
    return s if len(s) <= n else s[:n] + "..."

def _search_nearest_line(lines: list[str], keyword: str, center_lineno: int,
                         radius: int = 30) -> str:
    """在 center_lineno 上下 radius 行内搜索与 keyword 最相似的行。
    
    Args:
        lines: 文件所有行
        keyword: 要搜索的关键子串（已 strip）
        center_lineno: 中心行号（当前报错的行，搜索范围以此为中心）
        radius: 搜索半径行数，默认 ±30 行
    
    Returns:
        建议字符串，无匹配时返回空字符串。
    """
    if not keyword or not lines:
        return ""
    lo = max(0, center_lineno - radius - 1)
    hi = min(len(lines), center_lineno + radius)
    best = (0.0, 0, "")  # (ratio, lineno, line)
    for i in range(lo, hi):
        lineno = i + 1
        if lineno == center_lineno:
            continue
        stripped = lines[i].strip()
        if not stripped:
            continue
        if keyword in stripped:
            ratio = 1.0
        else:
            ratio = SequenceMatcher(None, keyword, stripped).ratio()
        if ratio > best[0]:
            best = (ratio, lineno, lines[i])
    if best[1] == 0 or best[0] < 0.4:
        return ""
    # ±1 行漂移：行号恰好相邻且相似度很高 → 直接提示可能的正确行号
    drift = best[1] - center_lineno
    if abs(drift) == 1 and best[0] >= 0.9:
        direction = f"+{drift}" if drift > 0 else str(drift)
        return (f"\n💡 可能是行号漂移 {direction} 行（经常因文件增删行导致）："
                f"\n  L{best[1]}: {_trunc(best[2].strip(), 80)}")
    return (f"\n💡 附近最相似的行在 L{best[1]}（相似度 {best[0]:.0%}，偏移 {drift:+d} 行）："
            f"\n  {_trunc(best[2].strip(), 80)}")

def _find_similar_lines(text: str, target: str, max_show: int = 3) -> str:
    """在文本中查找与 target（可能多行）最相似的位置，返回线索。"""
    lines = text.splitlines()
    if not lines:
        return ""

    # 多行 target：逐行诊断匹配情况，给出具体失败原因
    target_lines = target.strip().splitlines()
    if len(target_lines) > 1:
        # 对每个 target 行找最佳匹配
        per_line_results = []  # (tline_idx, best_ratio, best_lineno, best_line)
        for ti, tline in enumerate(target_lines):
            tline = tline.strip()
            if not tline:
                per_line_results.append((ti, 0, None, None))
                continue
            best = (0, None, None)
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                ratio = SequenceMatcher(None, tline, stripped).ratio()
                if ratio > best[0]:
                    best = (ratio, i, line)
            per_line_results.append((ti, best[0], best[1], best[2]))

        # 找出所有目标行中最佳匹配的 top max_show（原有行为，兼容旧调用方）
        scored = []
        for ti, ratio, lineno, line in per_line_results:
            if lineno is not None and ratio > 0.4:
                scored.append((ratio, lineno, line))
        scored.sort(key=lambda x: -x[0])
        seen = set()
        unique = []
        for ratio, lineno, line in scored:
            if lineno not in seen:
                seen.add(lineno)
                unique.append((ratio, lineno, line))

        # 构建诊断输出
        out = ""
        # 1) target 行数概览
        non_empty_target = [tl for tl in target_lines if tl.strip()]
        out += f"目标共 {len(target_lines)} 行（非空 {len(non_empty_target)} 行），文件中 {len(lines)} 行。\n"

        # 2) 逐行诊断（最多展示 max_show 行）
        failed_lines = []
        for ti, ratio, lineno, line in per_line_results:
            if not target_lines[ti].strip():
                continue  # 跳过空行
            if ratio < 0.4:
                failed_lines.append(ti + 1)
        if failed_lines:
            out += f"⚠ 第 {', '.join(str(i) for i in failed_lines[:5])} 行"
            if len(failed_lines) > 5:
                out += f"（共 {len(failed_lines)} 行）"
            out += " 未找到相似行（ratio < 0.4）\n"

        # 3) 如果所有行都找到了相似行但精确匹配仍失败 → 缩进/空格/换行符差异
        if not failed_lines and len(non_empty_target) > 1:
            out += "ℹ 所有行单独匹配均成功，但整体精确匹配失败。\n"
            # 逐行 diff：对比每个 target 行与其最佳匹配行（行号去重）
            diff_count = 0
            shown_lines = set()
            for ti, ratio, lineno, line in per_line_results:
                if diff_count >= 5:
                    out += "  ...（差异过多，省略后续）\n"
                    break
                tline = target_lines[ti]
                if not tline.strip() or lineno is None:
                    continue
                if lineno in shown_lines:
                    continue  # 同一行已展示过差异
                t_r = tline.rstrip()
                l_r = line.rstrip()
                t_indent = len(tline) - len(tline.lstrip())
                l_indent = len(line) - len(line.lstrip())
                if tline == line:
                    continue  # 完全相同，跳过
                shown_lines.add(lineno)
                diff_count += 1
                if t_indent != l_indent:
                    out += f"  L{lineno}: 缩进差异（期望 {t_indent}空格，实际 {l_indent}空格）\n"
                elif t_r != l_r:
                    out += f"  L{lineno}: 内容差异 → 期望: {_trunc(t_r, 50)} | 实际: {_trunc(l_r, 50)}\n"
                else:
                    t_trail = len(tline) - len(t_r)
                    l_trail = len(line) - len(l_r)
                    out += f"  L{lineno}: 行尾空格差异（期望 {t_trail}，实际 {l_trail}）\n"
            out += "💡 建议：修正 content 后重试，或使用行号模式（start_line+end_line，不传 start_match/end_match 则跳过校验）直接按位置替换。\n"

        # 4) 最相似行列表（原有兼容输出）
        if unique:
            out += "📎 文件中最相似的行:\n"
            for ratio, lineno, line in unique[:max_show]:
                out += f"  L{lineno}: {_trunc(line, 80)}  (相似度 {ratio:.0%})\n"
        elif not failed_lines:
            out += "（无相似行）\n"
        return out

    # 单行 target：原有逻辑 + SequenceMatcher 提高准确度
    target_stripped = target.strip()
    scored = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        # 先用前缀匹配快速筛选
        prefix_score = 0
        for a, b in zip(target_stripped, stripped):
            if a == b:
                prefix_score += 1
            else:
                break
        prefix_ratio = prefix_score / max(len(target_stripped), 1)
        if prefix_ratio < 0.3:
            # 前缀不匹配再用 SequenceMatcher 兜底
            ratio = SequenceMatcher(None, target_stripped, stripped).ratio()
            if ratio > 0.4:
                scored.append((ratio, i, line))
        else:
            scored.append((prefix_ratio, i, line))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return ""
    out = ""
    for score, lineno, line in scored[:max_show]:
        out += f"  L{lineno}: {_trunc(line, 80)}\n"
    return out



# ══════════════════════════════════════════════════════
# 各 action 的独立实现函数（从 file_operation 中提取）
# ══════════════════════════════════════════════════════

def _read_path(p: Path, src: str, offset: int, limit: int, show_line_numbers: bool, skip_dirs_set: set) -> str:
    """read 操作：读文件（默认带行号）/ 列目录。"""
    if not p.exists():
        return f"路径不存在: {src}"
    if limit < 0:
        return "limit 不能为负数"
    if p.is_dir():
        res = ",".join(
            str(x)
            for x in p.iterdir()
            if x.name not in skip_dirs_set
        )
        return res if res else "这是空目录,什么都没有"
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    total_lines = len(lines)
    start = max(0, offset - 1)
    sliced = lines[start:start + limit]

    if show_line_numbers:
        result = "\n".join(
            f"{i}|{line}"
            for i, line in enumerate(sliced, offset)
        )
    else:
        result = "\n".join(sliced)

    if start + limit < total_lines:
        start_line = offset
        end_line = start_line + len(sliced) - 1
        if start_line == 1:
            result += f"\n\n⚠️ 文件共 {total_lines} 行，已显示前 {end_line} 行。"
        else:
            result += f"\n\n⚠️ 文件共 {total_lines} 行，当前显示第 {start_line}-{end_line} 行。"
        result += " 用 offset 指定起始行，或加大 limit。"
    return result


def _search_path(p: Path, src: str, content: str, regex: bool, recursive: bool,
                 file_ext: Optional[str], ignore_case: bool, max_files: int,
                 skip_dirs_set: set) -> str:
    """search 操作：搜索文件内容，默认递归子目录，支持正则 + 扩展名过滤。"""
    if not p.exists():
        return f"路径不存在: {src}"
    if not content:
        return "search 操作需要 content（搜索关键词或正则）"

    # 编译正则
    try:
        flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
        if regex:
            search_re = re.compile(content, flags)
        else:
            search_re = re.compile(re.escape(content), flags)
    except re.error as e:
        return f"正则表达式无效: {e}"

    results: list[str] = []
    file_count = 0

    SKIP_DIRS = skip_dirs_set

    def search_file(file_path: Path):
        nonlocal file_count
        file_count += 1
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return
        for i, line in enumerate(text.splitlines(), 1):
            if search_re.search(line):
                results.append(f"{file_path}:{i}:{line.rstrip()}")

    if p.is_file():
        search_file(p)
    elif p.is_dir():
        if recursive:
            for dp, dn, fn in os.walk(p):
                dn[:] = [d for d in dn if d not in SKIP_DIRS]
                for f in fn:
                    if search_re.search(f):
                        results.append(f"[文件名匹配] {Path(dp) / f}")
                    if file_ext and not f.endswith(file_ext):
                        continue
                    if file_count >= max_files:
                        break
                    fp = Path(dp) / f
                    search_file(fp)
                if file_count >= max_files:
                    break
            if file_count >= max_files:
                results.append(f"⚠️ 已扫描 {max_files} 个文件（达上限），停止继续扫描。如需扩大范围请缩小 src 目录或用 file_ext 过滤")
        else:
            for fpath in p.iterdir():
                if fpath.is_file() and fpath.name not in SKIP_DIRS:
                    if search_re.search(fpath.name):
                        results.append(f"[文件名匹配] {fpath}")
                    if file_ext and not fpath.name.endswith(file_ext):
                        continue
                    if file_count >= max_files:
                        break
                    search_file(fpath)
            if file_count >= max_files:
                results.append(f"⚠️ 已扫描 {max_files} 个文件（达上限），停止继续扫描。如需扩大范围请缩小 src 目录或用 file_ext 过滤")

    if not results and not regex and _REGEX_CHARS.search(content):
        # 普通文本模式未匹配到，但 content 含正则特殊字符 → 递归调用自身，regex=True
        return (
            "[普通文本模式未匹配到，检测到正则特殊字符，自动切换为正则模式再次搜索：]\n"
            + _search_path(
                p, src, content, regex=True,
                recursive=recursive, file_ext=file_ext, ignore_case=ignore_case,
                max_files=max_files, skip_dirs_set=skip_dirs_set,
            )
        )
    if not results:
        scope = "递归" if recursive else "当前目录"
        return f"{scope}搜索未找到匹配 \"{_trunc(content)}\" 的内容"
    return "\n".join(results)


def _write_path(p: Path, src: str, content: str, if_not_exists: bool) -> str:
    """write 操作：覆盖写文件，自动创建父目录。"""
    with _get_file_lock(p):
        if if_not_exists and p.exists():
            return f"⚠️ 文件已存在，跳过创建（if_not_exists=True）: {src}"
        p.parent.mkdir(parents=True, exist_ok=True)
        err = _atomic_write(p, content)
        if err:
            return err
        lines = content.count("\n") + (0 if content.endswith("\n") else 1) if content else 0
        return f"✅ 已写入: {src}（{len(content)} 字符，{lines} 行）"


def _append_path(p: Path, src: str, content: str) -> str:
    """append 操作：追加到文件末尾。"""
    with _get_file_lock(p):
        if not p.exists():
            return f"文件不存在: {src}"
        before_size = p.stat().st_size
        try:
            with open(p, "a", encoding="utf-8", newline="") as f:
                f.write(content)
        except PermissionError:
            return f"❌ 追加失败：文件被占用或无权限: {src}"
        except OSError as e:
            return f"❌ 追加失败：{e}"
        after_size = p.stat().st_size
        written_bytes = after_size - before_size
        if written_bytes != len(content.encode("utf-8")):
            return f"⚠️ 追加警告：预期写入 {len(content.encode('utf-8'))} 字节，实际增加 {written_bytes} 字节"
        return f"✅ 已追加: {src}（+{len(content)} 字符）"


def _delete_path(p: Path, src: str) -> str:
    """delete 操作：删除文件或递归删除目录。"""
    with _get_file_lock(p):
        if not p.exists():
            return f"路径不存在: {src}"
        if p.is_file():
            p.unlink()
            return f"已删除文件: {src}"
        if p.is_dir():
            shutil.rmtree(p)
            return f"已删除目录: {src}"


def _copy_path(p: Path, src: str, dst: Optional[str]) -> str:
    """copy 操作：复制文件或目录。"""
    if not dst:
        return "copy 操作需要 dst"
    if not p.exists():
        return f"源路径不存在: {src}"
    dst_p = get_path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        shutil.copy2(p, dst_p)
    else:
        shutil.copytree(p, dst_p)
    return f"已复制: {src} -> {dst}"


def _move_path(p: Path, src: str, dst: Optional[str]) -> str:
    """move 操作：移动/重命名。"""
    if not dst:
        return "move 操作需要 dst"
    if not p.exists():
        return f"源路径不存在: {src}"
    dst_p = get_path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(p, dst_p)
    return f"已移动: {src} -> {dst}"


def _create_path(p: Path, src: str, content: str) -> str:
    """create 操作：有后缀 → 创建文件，无后缀 → 创建目录。"""
    if p.suffix:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        if content:
            p.write_text(content, encoding="utf-8")
            return f"✅ 已创建并写入: {src}（{len(content)} 字符）"
        return f"已创建文件: {src}"
    else:
        p.mkdir(parents=True, exist_ok=True)
        return f"已创建目录: {src}"


def _replace_path(p: Path, src: str, content: str, new_content: Optional[str],
                  regex: bool, ignore_case: bool, dry_run: bool,
                  match_index: Optional[int], replace_all: bool,
                  start_line: Optional[int], end_line: Optional[int],
                  start_match: Optional[str], end_match: Optional[str]) -> str:
    """replace 操作：精确替换。content 模式 / 行号模式。"""
    if not p.exists():
        return f"文件不存在: {src}"
    if new_content is None:
        return "replace 操作需要 new_content（替换后的新内容）"

    # ── 行号模式：按行号定位 + 起止行强制校验 ──
    # start_match/end_match 必须提供：
    #   ""        = 校验该行 strip 后为空（真正空行或纯空格行）
    #   "关键字"  = 子串包含校验，该行 strip 后需包含此关键字
    if start_line is not None:
        if content and content.strip():
            return "行号模式（start_line）与 content 模式互斥，请二选一"
        if end_line is None:
            return "行号模式需要 end_line"
        if start_line < 1 or end_line < start_line:
            return f"行号范围无效: start_line={start_line}, end_line={end_line}"
        if start_match is None or end_match is None:
            return "行号模式必须提供 start_match 和 end_match 进行内容校验（传 '' 表示校验空行），防止行号漂移导致改错位置"

        # 全程持锁（读→校验→写），与 content 模式一致，防止并发 replace 读到旧快照互相覆盖
        with _get_file_lock(p):
            raw = p.read_text(encoding="utf-8").replace('\r\n', '\n')
            lines = raw.split('\n')
            if end_line > len(lines):
                return f"end_line={end_line} 超出文件总行数 {len(lines)}"

            # ── 校验单行（抽取公共函数，统一 start/end 处理）──
            def _check_line(actual_raw: str, expected: str, label: str, lineno: int) -> str | None:
                """校验单行内容。空字符串精确匹配空行，非空子串包含校验。
                   返回 None=通过，否则返回错误信息。"""
                actual = actual_raw.strip()
                exp = expected.strip()
                # 空字符串：精确匹配空行（允许文件行是纯空格/Tab）
                if not exp:
                    if not actual:
                        return None  # 通过：预期空行，实际也是空行
                    return (f"❌ {label} 校验失败（预期空行但实际有内容）："
                            f"\n  L{lineno} 预期: 空行（传 '' 表示 strip 后为空）"
                            f"\n  实际: {_trunc(actual_raw, 80)}"
                            f"\n💡 请检查行号是否漂移（文件已增删行），或改为该行内容的关键子串")
                # 非空：子串包含校验
                if exp in actual:
                    return None  # 通过
                # 失败 → 搜索文件中最相似的行，给出行号漂移建议
                suggestion = _search_nearest_line(lines, exp, center_lineno=lineno)
                return (f"❌ {label} 校验失败（文件可能已被修改）："
                        f"\n  L{lineno} 预期包含: \"{_trunc(exp, 60)}\""
                        f"\n  实际: {_trunc(actual_raw, 80)}"
                        f"{suggestion}")

            # 校验 start 行
            start_err = _check_line(lines[start_line - 1], start_match, "起始行", start_line)
            if start_err:
                return start_err
            # 校验 end 行
            end_err = _check_line(lines[end_line - 1], end_match, "结束行", end_line)
            if end_err:
                return end_err

            new_lines = lines[:start_line - 1] + [new_content] + lines[end_line:]
            new_text = '\n'.join(new_lines)
            if dry_run:
                old_block = '\n'.join(lines[start_line - 1:end_line])
                return f"[预览] L{start_line}-L{end_line}:\n{_trunc(old_block, 200)}\n→\n{_trunc(new_content, 200)}"
            with _get_file_lock(p):
                err = _atomic_write(p, new_text)
                if err:
                    return err.replace("写入失败", "替换失败").replace("写入验证失败", "替换写入验证失败")
            return f"✅ 已替换 L{start_line}-L{end_line}（共 {end_line - start_line + 1} 行）"

    # ── content 模式：全文搜索 ──
    if not content:
        return "replace 操作需要 content（原内容/匹配目标），或使用行号模式（start_line+end_line+start_match+end_match，含强制校验）"
    if match_index is not None and replace_all:
        return "match_index 与 replace_all 不能同时使用"
    if match_index is not None and match_index < 1:
        return "match_index 必须 >= 1"

    # 读→改→写全过程持锁，防止并发 replace 读到同一份旧快照互相覆盖
    _content_lock = _get_file_lock(p)
    _content_lock.acquire()
    try:
        text = p.read_text(encoding="utf-8")

        # ── 换行符归一化：消除 \r\n vs \n 导致的匹配失败 ──
        text = text.replace('\r\n', '\n')
        content = content.replace('\r\n', '\n')

        # ── 计数 + 定位 ──
        if regex:
            flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
            matches = list(re.finditer(content, text, flags))
            count = len(matches)
        else:
            if ignore_case:
                count = text.lower().count(content.lower())
            else:
                count = text.count(content)

        # ── 0 匹配时，多行目标自动尝试首尾空格归一化 ──
        target_line_count = len(content.strip().splitlines())
        auto_fixed = False
        if count == 0 and not regex and target_line_count > 1:
            content_lines = content.split('\n')
            content_stripped = [line.strip() for line in content_lines]
            text_lines = text.split('\n')
            text_stripped = [line.strip() for line in text_lines]

            matches_in_text = []
            for start_idx in range(len(text_stripped) - len(content_stripped) + 1):
                if ignore_case:
                    cmp = [tl.lower() for tl in text_stripped[start_idx:start_idx + len(content_stripped)]]
                    target_cmp = [cl.lower() for cl in content_stripped]
                    if cmp == target_cmp:
                        matched = '\n'.join(text_lines[start_idx:start_idx + len(content_stripped)])
                        matches_in_text.append(matched)
                else:
                    if text_stripped[start_idx:start_idx + len(content_stripped)] == content_stripped:
                        matched = '\n'.join(text_lines[start_idx:start_idx + len(content_stripped)])
                        matches_in_text.append(matched)

            if matches_in_text:
                content = matches_in_text[0]
                count = len(matches_in_text)
                auto_fixed = True

        # ── 仍然 0 匹配 → 报错 ──
        if count == 0:
            msg = f"未匹配到指定内容: {_trunc(content)}"
            if not regex and _REGEX_CHARS.search(content):
                msg += (
                    "\n💡 提示：当前为普通文本匹配（默认），但 \"{content}\" 中含正则特殊字符（如 `|` `.` `*` `(` `)`）"
                    "\n  · 如果要用正则表达式匹配 → 传 regex=True".format(content=_trunc(content))
                )
            similar = _find_similar_lines(text, content)
            if similar:
                msg += "\n" + similar
            if target_line_count > 1:
                msg += (
                    "\n💡 多行匹配提示：目标有 {} 行，请检查缩进层级、行首/行尾空格、\\r\\n vs \\n 是否一致。已自动归一化 \\r\\n → \\n。"
                    "\n⚠️ 诊断规则说明：上方的「逐行诊断」采用宽松行匹配(strip + 相似度)，仅用于辅助定位；"
                    "实际 replace 为严格精确匹配（含所有空白字符），请重点检查行尾多余空格、tab/空格混用。"
                ).format(target_line_count)
            else:
                msg += "\n💡 提示：请检查缩进、空格、换行符是否与源文件一致。已自动归一化 \\r\\n → \\n"
            return msg

        # ── 辅助：定位第 N 个匹配的行号 ──
        def _locate_match(idx: int) -> str:
            """返回第 idx 个匹配的位置描述（1-based）"""
            if regex:
                m = matches[idx - 1]
                line_no = text[:m.start()].count("\n") + 1
                context = _trunc(text[max(0, m.start() - 20):m.end() + 20].replace("\n", "\\n"), 80)
                return f"L{line_no}: ...{context}..."
            else:
                search_str = content
                pos = -1
                for _ in range(idx):
                    if ignore_case:
                        pos = text.lower().find(search_str.lower(), pos + 1)
                    else:
                        pos = text.find(search_str, pos + 1)
                if pos >= 0:
                    line_no = text[:pos].count("\n") + 1
                    context = _trunc(text[max(0, pos - 20):pos + len(search_str) + 20].replace("\n", "\\n"), 80)
                    return f"L{line_no}: ...{context}..."
                return f"第{idx}处"

        # ── 多处匹配 ──
        if count > 1:
            if match_index is not None:
                if match_index > count:
                    return f"match_index={match_index} 超出范围，仅有 {count} 处匹配"
            elif replace_all:
                pass
            elif dry_run:
                lines_list = []
                lines_list.append(f"共匹配到 {count} 处：")
                for i in range(1, min(count, 10) + 1):
                    lines_list.append(f"  [{i}] {_locate_match(i)}")
                if count > 10:
                    lines_list.append(f"  ... 还有 {count - 10} 处")
                lines_list.append(f"\n请使用 match_index=N 指定替换位置，或 replace_all=true 全部替换")
                return "\n".join(lines_list)
            else:
                lines_list = []
                lines_list.append(f"共匹配到 {count} 处。请扩大 content 上下文使其唯一，或使用以下选项：")
                for i in range(1, min(count, 5) + 1):
                    lines_list.append(f"  [{i}] {_locate_match(i)}")
                if count > 5:
                    lines_list.append(f"  ... 还有 {count - 5} 处（用 dry_run=true 查看全部）")
                lines_list.append(f"\n用法：match_index=N 指定第 N 处，或 replace_all=true 全部替换")
                return "\n".join(lines_list)

        # ── 执行替换 ──
        if dry_run:
            if count == 1:
                return f"[DRY-RUN] 替换 {_locate_match(1)}\n  {_trunc(content)} → {_trunc(new_content)}"
            if replace_all:
                return f"[DRY-RUN] 将替换全部 {count} 处"
            return f"[DRY-RUN] 将替换第 {match_index} 处\n  {_trunc(content)} → {_trunc(new_content)}"

        if regex:
            flags = re.MULTILINE | (re.IGNORECASE if ignore_case else 0)
            if count == 1 or replace_all:
                sub_count = 0 if replace_all else 1
                new_text = re.sub(content, new_content, text, count=sub_count, flags=flags)
            else:
                m = matches[match_index - 1]
                new_text = text[:m.start()] + new_content + text[m.end():]
        else:
            if replace_all:
                if ignore_case:
                    new_text = re.sub(re.escape(content), new_content, text, flags=re.IGNORECASE)
                else:
                    new_text = text.replace(content, new_content)
            elif match_index is not None and match_index > 1:
                search_str = content
                pos = -1
                for _ in range(match_index):
                    if ignore_case:
                        pos = text.lower().find(search_str.lower(), pos + 1)
                    else:
                        pos = text.find(search_str, pos + 1)
                new_text = text[:pos] + new_content + text[pos + len(search_str):]
            else:
                if ignore_case:
                    idx = text.lower().index(content.lower())
                    new_text = text[:idx] + new_content + text[idx + len(content):]
                else:
                    new_text = text.replace(content, new_content, 1)

        # 原子写入：先写临时文件，成功后再替换原文件（内层 with 因 RLock 可重入，安全）
        with _get_file_lock(p):
            err = _atomic_write(p, new_text)
            if err:
                return err.replace("写入失败", "替换失败").replace("写入验证失败", "替换写入验证失败")
        auto_note = "（已自动去除行尾空格后匹配）" if auto_fixed else ""
        if replace_all:
            return f"✅ 替换成功: 全部 {count} 处{auto_note}"
        return f"✅ 替换成功: {_trunc(content)} -> {_trunc(new_content)}{auto_note}"
    finally:
        _content_lock.release()


def _stat_path(p: Path, src: str) -> str:
    """stat 操作：查看文件大小/修改时间等信息。"""
    if not p.exists():
        return f"路径不存在: {src}"
    s = p.stat()
    mtime = datetime.datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    ctime = datetime.datetime.fromtimestamp(s.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
    size_kb = s.st_size / 1024
    info = f"路径: {src}\n"
    info += f"类型: {'目录' if p.is_dir() else '文件'}\n"
    info += f"大小: {s.st_size} 字节 ({size_kb:.1f} KB)\n"
    info += f"修改时间: {mtime}\n"
    info += f"创建时间: {ctime}"
    return info

@tool(
    """统一文件系统操作工具。所有文件读写、搜索、替换、目录管理都用这一个工具，
不要绕 Python 脚本。

铁律：
- 路径始终用绝对路径，src/dst 都传完整绝对路径
- write 自动创建父目录，无需手动 mkdir
- delete 不可逆（删除文件或递归删除目录），调用前确认

action 速查：
  read    - 读文件（默认带行号）/ 列目录，自动跳过 .git/node_modules 等
  write   - 覆盖写文件，自动创建父目录
  append  - 追加到文件末尾
  delete  - 删除文件/目录（不可逆！）
  copy    - 复制文件/目录
  move    - 移动/重命名
  create  - 有后缀(.py/.txt) → 创建文件（可指定content写入），无后缀 → 创建目录
  search  - 搜索文件内容，默认递归子目录，支持正则 + 扩展名过滤
  replace - 精确替换。content 模式：全文搜索，多匹配时报错列位置；行号模式：start_line+end_line+start_match+end_match 按行号定位，起止子串校验防漂移（''=空行校验，'关键字'=子串包含，失败时搜索最相似行给行号建议）
  stat    - 查看文件大小/修改时间等信息
""")
def file_operation(
        action: Annotated[
            Literal["read", "write", "append", "delete", "copy", "move", "create", "search", "replace", "stat"],
            Field(description="操作类型,必须指定")
        ],
        src: Annotated[
            Union[str, list[str]],
            Field(description="源绝对路径。read/delete 支持字符串数组（遍历执行），其他 action 仅支持字符串")
        ],
        dst: Annotated[
            Optional[str],
            Field(description="目标绝对路径(copy/move时需要)")
        ] = None,
        content: Annotated[
            str,
            Field(description="write/append/create: 要写入的文本；search/replace: 要匹配的关键词或正则")
        ] = "",
        new_content: Annotated[
            Optional[str],
            Field(description="replace: 替换后的新内容")
        ] = None,
        offset: Annotated[
            int,
            Field(description="read起始行号（1-based），默认1")
        ] = 1,
        limit: Annotated[
            int,
            Field(description="read行数上限，默认 1500")
        ] = 1500,
        show_line_numbers: Annotated[
            bool,
            Field(description="read 时是否显示行号（1-based），默认 True")
        ] = True,
        regex: Annotated[
            bool,
            Field(description="search/replace 时 content 是否作为正则表达式，默认 False（普通文本匹配）")
        ] = False,
        dry_run: Annotated[
            bool,
            Field(description="replace 预览模式，不实际修改文件，默认 False")
        ] = False,
        recursive: Annotated[
            bool,
            Field(description="search 是否递归子目录，默认 True")
        ] = True,
        file_ext: Annotated[
            Optional[str],
            Field(description="search 扩展名过滤，如 '.py'、'.java'，默认不过滤")
        ] = None,
        ignore_case: Annotated[
            bool,
            Field(description="search/replace 是否忽略大小写，默认 False")
        ] = False,
        max_files: Annotated[
            int,
            Field(description="search 最大搜索文件数，默认 500")
        ] = 500,
        skip_dirs: Annotated[
            list[str],
            Field(description="read/search 列目录时要跳过的目录名列表，传空数组则不跳过任何目录")
        ] = ['.git', 'node_modules', '__pycache__', '.venv', 'vendor', 'target', 'build', 'dist', '.idea', '.vscode'],
        match_index: Annotated[
            Optional[int],
            Field(description="replace: 多处匹配时指定替换第几处（1-based），不传则列出所有匹配位置（含行号和上下文）")
        ] = None,
        replace_all: Annotated[
            bool,
            Field(description="replace: 替换所有匹配项，默认 False。与 match_index 互斥")
        ] = False,
        start_line: Annotated[
            Optional[int],
            Field(description="replace行号模式：起始行号（1-based），与 content 互斥")
        ] = None,
        end_line: Annotated[
            Optional[int],
            Field(description="replace行号模式：结束行号（1-based），必须 >= start_line")
        ] = None,
        start_match: Annotated[
            Optional[str],
            Field(description="replace行号模式：start_line 行的内容校验（必须提供。''=校验空行（strip后为空），'关键字'=子串包含校验）。不允许跳过！")
        ] = None,
        end_match: Annotated[
            Optional[str],
            Field(description="replace行号模式：end_line 行的内容校验（必须提供。''=校验空行（strip后为空），'关键字'=子串包含校验）。不允许跳过！")
        ] = None,
        if_not_exists: Annotated[
            bool,
            Field(description="write: 仅在文件不存在时创建，默认 False（始终覆盖）")
        ] = False,
):
    # ── 数组模式：read/delete 支持路径列表，逐条递归处理 ──
    if isinstance(src, list):
        if action not in ("read", "delete"):
            return f"数组 src 仅支持 read/delete 操作，当前 action={action}"
        if not src:
            return "空路径列表"
        results = []
        for s in src:
            r = file_operation(
                action=action, src=s,
                offset=offset, limit=limit,
                show_line_numbers=show_line_numbers,
                skip_dirs=skip_dirs,
            )
            results.append(f"━━━ {s} ━━━\n{r}")
        return "\n\n".join(results)

    p = get_path(src)
    _skip_dirs = set(skip_dirs)

    if action == "read":
        return _read_path(p, src, offset, limit, show_line_numbers, _skip_dirs)
    if action == "search":
        return _search_path(p, src, content, regex, recursive, file_ext, ignore_case, max_files, _skip_dirs)
    if action == "write":
        return _write_path(p, src, content, if_not_exists)
    if action == "append":
        return _append_path(p, src, content)
    if action == "delete":
        return _delete_path(p, src)
    if action == "copy":
        return _copy_path(p, src, dst)
    if action == "move":
        return _move_path(p, src, dst)
    if action == "create":
        return _create_path(p, src, content)
    if action == "replace":
        return _replace_path(p, src, content, new_content, regex, ignore_case, dry_run,
                            match_index, replace_all, start_line, end_line,
                            start_match, end_match)
    if action == "stat":
        return _stat_path(p, src)
    return f"不支持的操作: {action}"