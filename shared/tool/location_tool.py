"""
获取手机 GPS 位置工具。

通过 client_action 通用机制请求手机端获取位置坐标，
后端拿到坐标后可由 AI 调用 maps_regeocode 解析为具体地址。
"""
from ai_agent.utils import tool
from ai_agent.container import get_container


@tool("获取手机端 GPS 位置。请求手机获取当前经纬度坐标，返回坐标文本。"
      "获取成功后 AI 可调用 maps_regeocode 将坐标解析为地址。"
      "如果手机端未响应（超时 30 秒），返回错误信息。")
async def get_mobile_location():
    result = await get_container().client_action.request("get_location", timeout=30)
    if result.get("error"):
        return f"[无法获取位置: {result['error']}]"
    lat = result.get("lat")
    lng = result.get("lng")
    if lat is None or lng is None:
        return "[无法获取位置: 返回数据不完整]"
    accuracy = result.get("accuracy", "未知")
    loc_type = result.get("type", "未知")
    return f"经度{lng}, 纬度{lat} (精度: {accuracy}米, 来源: {loc_type})"
