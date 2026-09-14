"""
统一异常处理器
所有异常最终都返回：
    {
        "code": xxx,
        "message": "xxx",
        "data": null
    }
"""

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.exceptions.errors import BusinessError
from app.schemas.common import ResponseModel


def _json_response(code: int, message: str, status_code: int = 200) -> JSONResponse:
    """小工具：快速构造统一格式的 JSON 响应。"""
    return JSONResponse(
        status_code=status_code,
        content=ResponseModel(
            code=code,
            message=message,
            data=None,
        ).model_dump(),
    )


async def business_exception_handler(
    request: Request,
    exc: BusinessError,
) -> JSONResponse:
    """业务异常：Router/Service 主动抛出的 BusinessError。

    HTTP 状态码由异常自身决定：默认 200（业务码语义），
    VersionConflictError 等会覆盖为真实错误码（如 409）。
    """
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

    return _json_response(code=422, message=message, status_code=200)


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """兜底：未捕获的异常（数据库错误等）。"""
    return _json_response(
        code=50000,
        message="服务器内部错误",
        status_code=200,
    )
