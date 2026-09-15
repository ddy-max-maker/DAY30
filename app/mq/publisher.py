"""消息生产者：订单领域事件发布。

只负责构造消息 + 发送到 exchange，不修改数据库、不写业务逻辑。
调用方（order_service）必须在数据库事务 commit 成功之后再调用，
避免"消息已消费但事务回滚"的不一致。

异常策略（本阶段 MVP）：
- publish 失败时异常向上抛出，由调用方捕获并 logger.error；
- 不回滚已完成的支付订单（DB 事务与 MQ 不是同一事务）。
"""

import json
import logging

import aio_pika

from app.mq.rabbitmq import ORDER_EVENTS_EXCHANGE, get_order_events_exchange

logger = logging.getLogger("backend")

# 事件名与 routing_key 同名（topic exchange 按 key 路由）
ORDER_PAID_EVENT = "order.paid"
ORDER_PAID_ROUTING_KEY = "order.paid"


def build_order_paid_message(order_id: int, user_id: int) -> dict:
    """构造 order.paid 事件消息体（纯函数，方便单测）。

    只传明确的业务字段，禁止直接发送 ORM 对象。
    """
    return {
        "event": ORDER_PAID_EVENT,
        "order_id": order_id,
        "user_id": user_id,
    }


async def publish_order_paid_event(order_id: int, user_id: int) -> None:
    """发布 order.paid 事件到 order_events exchange。

    未连接 / 发送失败时抛异常，由调用方记录错误日志。
    """
    exchange = get_order_events_exchange()
    if exchange is None:
        raise RuntimeError("RabbitMQ 未连接，无法发布 order.paid 事件")

    message_body = build_order_paid_message(order_id, user_id)

    message = aio_pika.Message(
        body=json.dumps(message_body).encode("utf-8"),
        content_type="application/json",
        # 持久化消息：RabbitMQ 重启后不丢失（exchange/queue 均为 durable）
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )

    await exchange.publish(message, routing_key=ORDER_PAID_ROUTING_KEY)
    logger.info(
        "order_paid_event_published order_id=%s user_id=%s exchange=%s routing_key=%s",
        order_id,
        user_id,
        ORDER_EVENTS_EXCHANGE,
        ORDER_PAID_ROUTING_KEY,
    )
