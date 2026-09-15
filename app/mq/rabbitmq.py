"""RabbitMQ 异步连接管理（aio-pika）。

职责：
- 应用启动时创建一条长连接 + channel + topic exchange
- 应用关闭时释放连接
- 向 publisher / consumer 提供共享的 channel / exchange

设计要点：
- 使用 connect_robust：RabbitMQ 重启后自动恢复连接/拓扑，业务层不写重连逻辑
- 全局只维护一条连接，禁止每个请求新建连接（高并发下握手成本高）
- 所有操作都是 async，禁止在 async 路由里调用阻塞客户端
- 连接失败不阻断应用启动：支付主流程不依赖 MQ 可用性
  （publish 失败只记日志，不回滚订单）
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

# ---------- 拓扑常量 ----------
ORDER_EVENTS_EXCHANGE = "order_events"
ORDER_PAID_QUEUE = "order_paid_queue"

# ---------- 模块级单例（随应用生命周期创建/关闭）----------
_connection: aio_pika.RobustConnection | None = None
_channel: aio_pika.abc.AbstractRobustChannel | None = None
_order_events_exchange: aio_pika.abc.AbstractRobustExchange | None = None


def build_amqp_url() -> str:
    """构造 AMQP URL，用户名/密码做 URL 编码（兼容特殊字符）。"""
    user = quote(RABBITMQ_USER, safe="")
    password = quote(RABBITMQ_PASSWORD, safe="")
    return f"amqp://{user}:{password}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"


async def connect() -> None:
    """创建长连接、channel，声明 topic exchange。

    幂等：已连接时直接返回。
    RABBITMQ_ENABLED=false（测试环境）时跳过。
    """
    global _connection, _channel, _order_events_exchange

    if not RABBITMQ_ENABLED:
        logger.info("rabbitmq_disabled_by_config")
        return

    if _connection is not None and not _connection.is_closed:
        return

    url = build_amqp_url()
    logger.info("rabbitmq_connecting host=%s port=%s", RABBITMQ_HOST, RABBITMQ_PORT)

    # connect_robust：断线自动重连并恢复 channel / exchange / queue 声明
    _connection = await aio_pika.connect_robust(url)
    _channel = await _connection.channel()

    # topic exchange：按 routing key 路由，方便后续扩展 order.cancelled 等事件
    _order_events_exchange = await _channel.declare_exchange(
        ORDER_EVENTS_EXCHANGE,
        aio_pika.ExchangeType.TOPIC,
        durable=True,
    )
    logger.info("rabbitmq_connected exchange=%s", ORDER_EVENTS_EXCHANGE)


async def close() -> None:
    """关闭连接（应用 shutdown 时调用），幂等。"""
    global _connection, _channel, _order_events_exchange

    if _connection is not None and not _connection.is_closed:
        await _connection.close()
        logger.info("rabbitmq_closed")

    _connection = None
    _channel = None
    _order_events_exchange = None


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
