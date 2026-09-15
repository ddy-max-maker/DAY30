import asyncio
import contextlib
import logging

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from app.core.config import RABBITMQ_ENABLED
from app.exceptions.errors import BusinessError
from app.exceptions.handlers import (
    business_exception_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.middleware.request_log import request_log_middleware
from app.mq import consumer, rabbitmq
from app.routers.admin import router as admin_router
from app.routers.auth import router as auth_router
from app.routers.cart import router as cart_router
from app.routers.order import router as order_router
from app.routers.product import router as product_router
from app.routers.user import router as user_router

logger = logging.getLogger("backend")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：管理 RabbitMQ 长连接与消费者后台任务。

    - startup：建立 MQ 连接、启动 order.paid 消费者
    - shutdown：取消消费者任务、关闭连接

    MQ 启动失败不阻断应用（best-effort）：支付主流程不依赖 MQ，
    publish 失败只记录错误日志。
    """
    consumer_task: asyncio.Task | None = None

    if RABBITMQ_ENABLED:
        try:
            await rabbitmq.connect()
            consumer_task = asyncio.create_task(
                consumer.consume_order_paid_event(),
                name="order-paid-consumer",
            )
        except Exception:
            logger.exception("rabbitmq_startup_failed")

    yield

    # ---------- shutdown ----------
    if consumer_task is not None:
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task
    await rabbitmq.close()


app = FastAPI(title="Backend Learning API", lifespan=lifespan)


@app.get("/health")
def health():
    """存活探针：供 Docker healthcheck / 负载均衡使用。

    故意保持轻量（不查 MySQL/Redis）：
    探针高频调用，依赖状态应由各中间件自己的 healthcheck 报告。
    """
    return {"status": "ok"}


app.middleware("http")(request_log_middleware)

# ---------- 注册路由 ----------
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(product_router)
app.include_router(order_router)
app.include_router(admin_router)
app.include_router(cart_router)

# ---------- 注册异常处理器（覆盖 FastAPI 默认行为）----------
# 自定义业务异常
app.add_exception_handler(BusinessError, business_exception_handler)

# FastAPI 内置 HTTPException（兼容老代码）
app.add_exception_handler(HTTPException, http_exception_handler)

# 参数校验失败（422）
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# 兜底：所有未捕获异常
app.add_exception_handler(Exception, unhandled_exception_handler)
