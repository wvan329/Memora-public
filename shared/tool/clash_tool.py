# tools/clash_tool.py
"""
Clash 代理切换工具 — 当外网访问失败时，自动从指定代理组中切换到一个可用的低延迟节点。
通过 Clash 内核（mihomo）的 Unix socket API 交互。
"""
import json
import re
from pathlib import Path
from typing import Annotated

import httpx
from pydantic import Field

from ai_agent.utils import tool

# ── 常量 ──────────────────────────────────────────────────────
_UNIX_SOCKET = "/tmp/verge/verge-mihomo.sock"
_HTTP_ENDPOINT = "http://127.0.0.1:9097"
_CONFIG_YAML = Path.home() / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/config.yaml"
_DEFAULT_SECRET = "set-your-secret"
_DEFAULT_GROUP = "🔰国外流量"


def _read_secret() -> str:
    """从 Clash config.yaml 读取 API secret，读不到用默认值。"""
    try:
        if _CONFIG_YAML.exists():
            text = _CONFIG_YAML.read_text(encoding="utf-8")
            m = re.search(r"^secret:\s*(.+)$", text, re.MULTILINE)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return _DEFAULT_SECRET


def _extract_delay(proxy: dict) -> float:
    """从代理节点的 history 中提取最新有效延迟（ms），无效返回无限大。"""
    history = proxy.get("history", [])
    if not history:
        return float("inf")
    # history 按时间倒序，取最新的
    delay = history[-1].get("delay", 0)
    if delay <= 0:
        return float("inf")
    return float(delay)


def _build_client() -> httpx.AsyncClient:
    """创建 httpx 客户端，优先 Unix socket，不可用时回退 HTTP。"""
    import os
    if os.path.exists(_UNIX_SOCKET):
        transport = httpx.AsyncHTTPTransport(uds=_UNIX_SOCKET)
        return httpx.AsyncClient(transport=transport, timeout=10.0)
    return httpx.AsyncClient(base_url=_HTTP_ENDPOINT, timeout=10.0)


@tool("""切换 Clash 代理组的选中节点——当外网访问失败、网络不通时调用。

从指定代理组中自动选择一个可用的低延迟节点进行切换。
默认操作「🔰国外流量」组，可指定偏好地区（如 新加坡、香港、日本）来缩小范围。

返回值包含切换结果：旧节点 → 新节点，新节点延迟（ms），以及候选节点数。
""")
async def switch_clash_node(
    group_name: Annotated[
        str,
        Field(description="要切换的代理组名，默认「🔰国外流量」")
    ] = _DEFAULT_GROUP,
    prefer_region: Annotated[
        str | None,
        Field(description="偏好地区关键词，如「新加坡」「香港」「日本」「美国」。为空则全局选择")
    ] = None,
) -> str:
    try:
        secret = _read_secret()
        auth_headers = {"Authorization": f"Bearer {secret}"}

        async with _build_client() as client:
            # ── 1. 获取所有代理信息 ──
            resp = await client.get("http://localhost/proxies", headers=auth_headers)
            if resp.status_code != 200:
                return f"❌ Clash API 返回状态码 {resp.status_code}: {resp.text[:200]}"
            all_proxies = resp.json().get("proxies", {})

            # ── 2. 定位目标代理组 ──
            group = all_proxies.get(group_name)
            if not group:
                return f"❌ 未找到代理组「{group_name}」"

            if group.get("type") != "Selector":
                return f"❌「{group_name}」不是 Selector 类型，无法切换"

            now = group.get("now", "")
            all_names = group.get("all", [])
            if not all_names:
                return f"❌ 代理组「{group_name}」内无可用节点"

            # ── 3. 筛选候选节点 ──
            candidates: list[tuple[str, float, bool]] = []  # (name, delay, alive)
            for name in all_names:
                if name == now:
                    continue  # 排除当前节点
                proxy = all_proxies.get(name)
                if proxy is None:
                    continue  # 不在 proxies 中（可能是子组）
                alive = proxy.get("alive", False)
                delay = _extract_delay(proxy)
                # 偏好地区过滤
                if prefer_region and prefer_region not in name:
                    continue
                candidates.append((name, delay, alive))

            if not candidates:
                region_hint = f"（偏好: {prefer_region}）" if prefer_region else ""
                return f"⚠️ 代理组「{group_name}」{region_hint}内没有可用的候选节点（排除当前节点「{now}」后）"

            # ── 4. 排序：优先 alive 且延迟低的 ──
            candidates.sort(key=lambda x: (not x[2], x[1]))
            best = candidates[0]
            best_name, best_delay, best_alive = best

            # ── 5. 切换 ──
            put_body = {"name": best_name}
            put_resp = await client.put(
                f"http://localhost/proxies/{group_name}",
                headers={**auth_headers, "Content-Type": "application/json"},
                content=json.dumps(put_body),
            )
            if put_resp.status_code not in (200, 204):
                return (
                    f"❌ 切换失败（HTTP {put_resp.status_code}）\n"
                    f"旧节点: {now}\n"
                    f"目标节点: {best_name}"
                )

            # ── 6. 构造结果 ──
            alive_str = "🟢" if best_alive else "🟡"
            delay_str = f"{best_delay:.0f}ms" if best_delay != float("inf") else "未知"
            total = len(candidates)
            region_hint = f"，偏好地区「{prefer_region}」" if prefer_region else ""

            lines = [
                f"✅ 已切换「{group_name}」节点{region_hint}",
                f"  旧节点: {now}",
                f"  新节点: {alive_str} {best_name}（延迟 {delay_str}）",
                f"  候选: {total} 个可用节点",
            ]
            return "\n".join(lines)

    except httpx.ConnectError:
        return (
            "❌ 无法连接 Clash 内核 API。请确认：\n"
            "1. Clash Verge 正在运行\n"
            f"2. Unix socket {_UNIX_SOCKET} 存在\n"
            "3. 外部控制器已启用"
        )
    except Exception as e:
        return f"❌ 切换节点异常: {type(e).__name__}: {e}"
