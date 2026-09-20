"""RabbitMQ 异步连接管理（aio-pika）。

职责：
- 应用启动时创建一条长连接 + channel（启用 publisher confirm）+ topic exchange
- 声明 DLX（死信交换机）供 consumer 发布死信消息
- 应用关闭时释放连接
- 向 publisher / consumer 提供共享的 channel / exchange

设计要点：
- 使用 connect_robust：RabbitMQ 重启后自动恢复连接/拓扑
- channel 显式启用 publisher_confirms：只有 Broker 确认接收才视为发布成功
- 全局只维护一条连接，禁止每个请求新建连接
- 所有操作都是 async，禁止在 async 路由里调用阻塞客户端
- 连接失败不阻断应用启动：支付主流程不依赖 MQ 可用性
"""

import logging
from urllib.parse import quote

import aio_pika

from app.core.config import (
    RABBITMQ_ENABLED,
    RABBITMQ_HOST,
    RABBITMQ_PASSWORD,
    RABBITMQ_PORT,
    RABBITMQ_USER,
)

logger = logging.getLogger("backend")

# ---------- 主拓扑 ----------
ORDER_EVENTS_EXCHANGE = "order_events"
ORDER_PAID_QUEUE = "order_paid_queue"

# ---------- 重试队列 ----------
# 消息进入 retry queue 后等待 RETRY_TTL_MS 毫秒，
# TTL 到期后由 DLX 自动路由回 order_events（routing_key=order.paid），
# 即重新进入主队列被消费者再次消费。
ORDER_PAID_RETRY_QUEUE = "order_paid_retry_queue"
RETRY_TTL_MS = 5000

# ---------- 死信 ----------
# 死信交换机：超过最大重试次数的消息通过它路由到 DLQ。
ORDER_EVENTS_DLX = "order_events_dlx"
ORDER_PAID_DLQ = "order_paid_dlq"
ORDER_PAID_DEAD_ROUTING_KEY = "order.paid.dead"

# ---------- 重试控制 ----------
# 消息 header 中的重试计数字段名；初始为 0（无此 header 视为 0）。
X_RETRY_COUNT = "x-retry-count"
MAX_RETRIES = 3

# ---------- 模块级单例（随应用生命周期创建/关闭）----------
_connection: aio_pika.RobustConnection | None = None
_channel: aio_pika.abc.AbstractRobustChannel | None = None
_order_events_exchange: aio_pika.abc.AbstractRobustExchange | None = None
_order_events_dlx: aio_pika.abc.AbstractRobustExchange | None = None


def build_amqp_url() -> str:
    """构造 AMQP URL，用户名/密码做 URL 编码（兼容特殊字符）。"""
    user = quote(RABBITMQ_USER, safe="")
    password = quote(RABBITMQ_PASSWORD, safe="")
    return f"amqp://{user}:{password}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"


async def connect() -> None:
    """创建长连接、channel（启用 publisher confirm），声明 exchange + DLX。

    幂等：已连接时直接返回。
    RABBITMQ_ENABLED=false（测试环境）时跳过。
    """
    global _connection, _channel, _order_events_exchange, _order_events_dlx

    if not RABBITMQ_ENABLED:
        logger.info("rabbitmq_disabled_by_config")
        return

    if _connection is not None and not _connection.is_closed:
        return

    url = build_amqp_url()
    logger.info("rabbitmq_connecting host=%s port=%s", RABBITMQ_HOST, RABBITMQ_PORT)

    # connect_robust：断线自动重连并恢复 channel / exchange / queue 声明
    _connection = await aio_pika.connect_robust(url)
    # publisher_confirms=True：只有 Broker 确认接收才视为发布成功
    # on_return_raises=True：mandatory 消息无法路由到任何 Queue 时抛 DeliveryError，
    #   覆盖"Broker 收到但消息丢失在路由环节"的缺口
    _channel = await _connection.channel(
        publisher_confirms=True,
        on_return_raises=True,
    )

    # 主 topic exchange
    _order_events_exchange = await _channel.declare_exchange(
        ORDER_EVENTS_EXCHANGE,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    # 死信交换机：consumer 发布超过最大重试次数的消息到此处
    _order_events_dlx = await _channel.declare_exchange(
        ORDER_EVENTS_DLX,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    logger.info(
        "rabbitmq_connected exchange=%s dlx=%s confirms=true on_return_raises=true",
        ORDER_EVENTS_EXCHANGE,
        ORDER_EVENTS_DLX,
    )


async def close() -> None:
    """关闭连接（应用 shutdown 时调用），幂等。"""
    global _connection, _channel, _order_events_exchange, _order_events_dlx

    if _connection is not None and not _connection.is_closed:
        await _connection.close()
        logger.info("rabbitmq_closed")

    _connection = None
    _channel = None
    _order_events_exchange = None
    _order_events_dlx = None


def is_enabled() -> bool:
    """MQ 开关是否打开（配置层）。"""
    return RABBITMQ_ENABLED


def is_connected() -> bool:
    """当前是否持有活动连接。"""
    return _connection is not None and not _connection.is_closed


def get_channel() -> aio_pika.abc.AbstractRobustChannel | None:
    """获取共享 channel（consumer 声明 queue 时使用）。"""
    return _channel


def get_order_events_exchange() -> aio_pika.abc.AbstractRobustExchange | None:
    """获取 order_events exchange（publisher 使用），未连接时返回 None。"""
    return _order_events_exchange


def get_order_events_dlx() -> aio_pika.abc.AbstractRobustExchange | None:
    """获取死信交换机（consumer 发布死信消息时使用），未连接时返回 None。"""
    return _order_events_dlx
