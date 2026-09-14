from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException

from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.order import router as order_router
from app.routers.product import router as product_router
from app.routers.todo import router as todo_router
from app.routers.user import router as user_router

from app.exceptions.handlers import (
    business_exception_handler,
    http_exception_handler,
    validation_exception_handler,
    unhandled_exception_handler,
)

from app.exceptions.errors import BusinessError

from app.middleware.request_log import (
    request_log_middleware
)


app = FastAPI(title="Backend Learning API")


@app.get("/health")
def health():
    """存活探针：供 Docker healthcheck / 负载均衡使用。

    故意保持轻量（不查 MySQL/Redis）：
    探针高频调用，依赖状态应由各中间件自己的 healthcheck 报告。
    """
    return {"status": "ok"}


app.middleware(
    "http"
)(
    request_log_middleware
)

# ---------- 注册路由 ----------
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(todo_router)
app.include_router(product_router)
app.include_router(order_router)
app.include_router(admin_router)

# ---------- 注册异常处理器（覆盖 FastAPI 默认行为）----------
# 自定义业务异常
app.add_exception_handler(BusinessError, business_exception_handler)

# FastAPI 内置 HTTPException（兼容老代码）
app.add_exception_handler(HTTPException, http_exception_handler)

# 参数校验失败（422）
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# 兜底：所有未捕获异常
app.add_exception_handler(Exception, unhandled_exception_handler)

