import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.request_context import request_id_context
from app.exceptions.errors import BusinessError
from app.schemas.common import ResponseModel

logger = logging.getLogger("backend")


def _json_response(code: int, message: str, status_code: int = 500) -> JSONResponse:
    """小工具：快速构造统一格式的 JSON 响应，并自动注入 request_id 到 header。

    默认 status_code=500（强制调用方显式传正确值，避免漏传导致 200）。
    exception_handler 走的是 Starlette ExceptionMiddleware，
    BaseHTTPMiddleware 在异常路径下无法可靠改写 response headers，
    所以在 handler 里直接设置 X-Request-ID 更稳。
    """
    response = JSONResponse(
        status_code=status_code,
        content=ResponseModel(
            code=code,
            message=message,
            data=None,
        ).model_dump(),
    )
    response.headers["X-Request-ID"] = request_id_context.get() or "-"
    return response


async def business_exception_handler(
    request: Request,
    exc: BusinessError,
) -> JSONResponse:
    """业务异常：Router/Service 主动抛出的 BusinessError。

    HTTP 状态码由异常自身决定，必须反映真实 HTTP 语义
    （401 未认证、403 无权限、404 不存在、409 冲突等）。
    """
    # 业务异常属于预期内分支，INFO 级别即可，不打 traceback
    logger.info(
        "business_exception code=%s status_code=%s message=%s",
        exc.code,
        exc.status_code,
        exc.message,
    )
    return _json_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
    )


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """FastAPI 内置 HTTPException（兼容旧代码）：
    把默认的 {"detail": "..."} 转换成统一结构。"""

    # 常见 HTTP 状态码 -> 业务码 & 中文消息
    status_to_biz = {
        400: (10000, "请求参数错误"),
        401: (10001, "未授权或登录已失效"),
        403: (10001, "无权限访问"),
        404: (10003, "资源不存在"),
        409: (10002, "资源冲突"),
        429: (10000, "请求过于频繁"),
    }

    code, default_msg = status_to_biz.get(
        exc.status_code,
        (10000, "服务异常"),
    )

    message = exc.detail if isinstance(exc.detail, str) and exc.detail else default_msg

    # 4xx 是客户端错误，WARNING；5xx 是服务端错误，ERROR
    if exc.status_code >= 500:
        logger.error(
            "http_exception status_code=%s detail=%s",
            exc.status_code,
            exc.detail,
            exc_info=exc,
        )
    else:
        logger.warning(
            "http_exception status_code=%s detail=%s",
            exc.status_code,
            exc.detail,
        )

    return _json_response(code=code, message=message, status_code=exc.status_code)


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """请求参数校验失败（422）：把 FastAPI 默认的 detail 数组展开成可读信息。"""

    errors = exc.errors()
    messages: list[str] = []

    for err in errors:
        # loc 形如 ("body", "email") 或 ("query", "page")
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "参数错误")
        if loc:
            messages.append(f"{loc}: {msg}")
        else:
            messages.append(msg)

    message = "; ".join(messages) if messages else "参数校验失败"

    # 用户传错参数，不是程序错误，INFO 级别即可
    logger.info("validation_failed message=%s", message)

    return _json_response(code=422, message=message, status_code=422)


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """兜底：未捕获的异常（数据库错误等）。

    HTTP 500 反映服务端错误语义，body.code=50000 是业务层细分码。
    使用 logger.exception 自动保留 traceback，
    通过 RequestIdFilter 自动关联 request_id。
    """
    logger.exception(
        "unhandled_exception path=%s method=%s", request.url.path, request.method
    )
    return _json_response(
        code=50000,
        message="服务器内部错误",
        status_code=500,
    )
