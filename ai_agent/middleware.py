"""
HTTP 中间件 —— 认证 + 缓存。
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from ai_agent.settings import settings

# 无需认证的路径前缀
_PUBLIC_PATHS = {"/", "/api/login", "/css", "/js", "/assets", "/ai/assets",
                 "/favicon.png", "/favicon.svg", "/icons.svg"}


async def auth_middleware(request: Request, call_next):
    """除白名单外所有请求需携带密码（Header X-Access-Password 或 ?password= 参数）。"""
    if not settings.access_password:
        return JSONResponse(status_code=401, content={"error": "密码错误"})

    path = request.url.path
    if (path in _PUBLIC_PATHS or
            path.startswith("/css/") or
            path.startswith("/js/") or
            path.startswith("/lib/") or
            path.startswith("/assets/") or
            path.startswith("/ai/assets/") or
            path.startswith("/ai/") or
            path.startswith("/ws")):
        return await call_next(request)

    password = request.headers.get("X-Access-Password", "") or request.query_params.get("password", "")
    if password != settings.access_password:
        return JSONResponse(status_code=401, content={"error": "请先登录"})

    return await call_next(request)


async def cache_middleware(request: Request, call_next):
    """第三方库（/lib/）设置一年强缓存，避免重复加载。"""
    response = await call_next(request)
    if request.url.path.startswith("/lib/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response
