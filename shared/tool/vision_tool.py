# tools/vision_tool.py
"""
视觉理解工具 — 调用百炼 Qwen3.7-Plus 多模态模型分析图片。

图片来源：
  - 公网 URL：直接传入
  - 本地绝对路径：压缩后上传到云服务器 upload-service → 公网 URL
  - "pick"：触发前端文件选择器 → 用户选择参数面板（描述/深度思考/高精度）
           → 前端根据参数压缩直传 upload-service → 返回 URL+用户参数
  - 列表：多张图片，每张独立上传

坐标说明（物体定位时）：
  - Qwen3.7/3.6/3.5/Qwen3-VL 返回归一化坐标 [0, 999]
  - 还原公式：x_px = x_norm / 999 × 原图宽度
"""
import os
import httpx
from io import BytesIO
from typing import Annotated, Optional, Union
from pydantic import Field
from PIL import Image

from ai_agent.utils import tool
from ai_agent.container import get_container
from ai_agent.settings import settings

UPLOAD_URL = "https://a.wgk-fun.top/upload"
UPLOAD_PASSWORD = settings.access_password

DEFAULT_QUESTION = "图中描绘的是什么？"


def _compress_image(input_path: str, max_size: int = 1000, quality: int = 50) -> BytesIO:
    """压缩图片到内存 BytesIO，返回 JPEG 字节流。quality 为 0-100。"""
    img = Image.open(input_path)
    if img.mode in ("RGBA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_size:
        img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, "JPEG", optimize=True, quality=quality)
    buf.seek(0)
    return buf


async def _upload_to_service(data: bytes, filename: str) -> str:
    """上传图片到云服务器 upload-service，返回公网 URL。"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            UPLOAD_URL,
            files={"files": (filename, data, "image/jpeg")},
            headers={"Authorization": f"Bearer {UPLOAD_PASSWORD}"},
        )
        resp.raise_for_status()
        return resp.json()["urls"][0]


async def _resolve_sources_with_options(
    image_sources,
    default_question: str = DEFAULT_QUESTION,
    default_vl_high_res: Optional[bool] = None,
) -> tuple[list[str], dict]:
    """统一图片来源为公网 URL 列表，同时返回用户在前端面板选择的参数。

    pick 模式：
      发送 pick_images_with_options 给前端，返回 {urls, question, vl_high_res}。
      AI 传入的参数仅作为前端面板的预填默认值。

    Returns:
      (urls, user_params)  — user_params 的 key 对齐 ai_vision_response 参数名
    """
    # 未显式指定时从 settings 读取默认值
    if default_vl_high_res is None:
        default_vl_high_res = settings.vision_high_res_default

    urls = []
    user_params = {
        "question": default_question,
        "vl_high_res": default_vl_high_res,
    }

    for src in image_sources:
        if src == "pick":
            result = await get_container().client_action.request(
                "pick_images_with_options", timeout=60,
                params={
                    "default_question": default_question,
                    "default_vl_high_res": default_vl_high_res,
                }
            )
            if "urls" in result:
                urls.extend(result["urls"])
                if "question" in result:
                    user_params["question"] = result["question"]
                if "vl_high_res" in result:
                    user_params["vl_high_res"] = result["vl_high_res"]
            elif "error" in result:
                raise RuntimeError(f"客户端选图失败: {result['error']}")
            else:
                raise RuntimeError(f"客户端选图返回异常: {result}")

        elif src.startswith("http://") or src.startswith("https://"):
            urls.append(src)

        elif os.path.isfile(src):
            pick_max = 2000 if default_vl_high_res else 1000
            pick_quality = 80 if default_vl_high_res else 50
            buf = _compress_image(src, max_size=pick_max, quality=pick_quality)
            filename = f"vision_{os.path.basename(src)}"
            url = await _upload_to_service(buf.read(), filename)
            urls.append(url)

        else:
            raise RuntimeError(f"图片不可用: {src}")

    return urls, user_params


@tool("""
支持：图像问答、物体定位（返回 0-999 归一化坐标）、OCR 文字提取、文档解析、视觉推理。
当需要识别图片内容时调用此工具。
注意：当用户说【上传图片】【帮我看下】【帮我看张图】【看看这个】【看下这个】【弹出图片选择器】等内容时立即调用此工具（image_source选择pick）
""")
async def vision_understand(
        image_source: Annotated[
            Union[str, list[str]], Field(description="图片：公网 URL、本地绝对路径、'pick'（弹窗选图）、或字符串列表（多张）")],
        question: Annotated[str, Field(
            description="要问的问题。")] = DEFAULT_QUESTION,
        # vl_high_res: Annotated[Optional[bool], Field(description="高分辨率模式。pick 模式下作为面板默认值")] = None,
) -> str:
    from ai_agent.ai_config import ai_vision_response
    from ai_agent.permissions import parent_queue
    from ai_agent.permissions import current_tool_call_id

    if isinstance(image_source, str):
        image_source = [image_source]

    # 未显式指定时从 settings 读取默认值
    # if vl_high_res is None:
    #     vl_high_res = settings.vision_high_res_default
    vl_high_res = settings.vision_high_res_default

    try:
        urls, user_params = await _resolve_sources_with_options(
            image_source,
            default_question=question,
            default_vl_high_res=vl_high_res,
        )
    except RuntimeError as e:
        return f"❌ {e}"

    if not urls:
        return "❌ 没有可用的图片"

    # 获取图片大小（并发 HEAD）
    async def _head_size(url: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.head(url)
                r.raise_for_status()
                s = int(r.headers.get("Content-Length", 0))
                if s >= 1048576: return f"{s / 1048576:.1f} MB"
                if s >= 1024:    return f"{s / 1024:.0f} KB"
                return f"{s} B"
        except Exception:
            return "未知大小"

    sizes = [await _head_size(u) for u in urls]
    images_meta = [{"url": u, "size": s} for u, s in zip(urls, sizes)]
    tc_id = current_tool_call_id.get()

    # 立即推送图片缩略图到前端工具卡片
    q = parent_queue.get()
    if q and tc_id:
        await q.put({
            "type": "vision_images",
            "images": images_meta,
            "tool_call_id": tc_id,
        })
        # 发弹窗开始标志（和 delegate_batch_start 同样逻辑）
        await q.put({
            "type": "vision_stream_start",
            "images": images_meta,
            "tool_call_id": tc_id,
            "question": user_params.get("question", question),
            "vl_high_res": user_params.get("vl_high_res", vl_high_res),
        })

    # 调用视觉模型（直接解包 user_params，key 已对齐）
    text_parts = []
    reasoning_parts = []
    try:
        async for chunk in ai_vision_response(
                image_urls=urls,
                **user_params,
        ):
            if chunk.get("type") == "text":
                text_parts.append(chunk["content"])
            elif chunk.get("type") == "reason":
                reasoning_parts.append(chunk["content"])
            if q and tc_id:
                await q.put({
                    "type": "vision_chunk",
                    "chunk": chunk,
                    "tool_call_id": tc_id,
                })
    except Exception as e:
        return f"❌ 视觉理解调用失败 [{type(e).__name__}]: {e}"

    result = "".join(reasoning_parts) + "".join(text_parts)

    import json
    return json.dumps({
        "type": "vision_result",
        "images": images_meta,
        "text": result,
    }, ensure_ascii=False)
