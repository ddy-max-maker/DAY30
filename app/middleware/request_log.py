import time

from fastapi import Request

from app.core.logger import logger
from app.core.request_context import (
    request_id_context,
    sanitize_request_id,
)


async def request_log_middleware(request: Request, call_next):
    # 1. 入口：取/生成 request_id 并 set 到 ContextVar
    # ContextVar.set() 返回 token，reset(token) 才能恢复原值
    client_request_id = request.headers.get("X-Request-ID")
    request_id = sanitize_request_id(client_request_id)
    token = request_id_context.set(request_id)

    # 2. 计时：用 perf_counter（monotonic），不用 wall-clock
    start_perf = time.perf_counter()

    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        # 3. 算耗时（无论成功失败都记一条完成日志）
        duration_ms = (time.perf_counter() - start_perf) * 1000

        # 4. 判断 status_code：
        #    - 正常路径：response 已赋值，用真实 status_code
        #    - 异常路径：response 为 None（异常被外层 handler 接管），
        #      本 middleware 拿不到真实 status，记 -1 表示"异常路径"
        if response is not None:
            status_code = response.status_code
            # 正常路径才设置 X-Request-ID（异常路径由 handler 自己设）
            response.headers["X-Request-ID"] = request_id
        else:
            status_code = -1

        # 5. 请求完成日志：method / path / status_code / duration_ms
        logger.info(
            "request_completed method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )

        # 6. 清理 ContextVar，防止上下文泄漏到下一个请求
        request_id_context.reset(token)
