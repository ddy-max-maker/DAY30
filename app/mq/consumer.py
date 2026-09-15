"""消息消费者：监听 order_paid_queue 并处理 order.paid 事件。

MVP 阶段：
- 收到消息后解析 JSON，打印结构化日志即视为消费成功
- 不实现重试、死信队列、幂等表（后续阶段单独实现）

生命周期：
- FastAPI startup 时由 lifespan 作为后台 asyncio.Task 启动
- shutdown 时 cancel 该任务（queue.consume 随 channel 关闭自动注销）
"""

import asyncio
import json
import logging

import aio_pika

from app.mq.publisher import ORDER_PAID_ROUTING_KEY
from app.mq.rabbitmq import (
    ORDER_EVENTS_EXCHANGE,
    ORDER_PAID_QUEUE,
    get_channel,
)

logger = logging.getLogger("backend")


async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    """处理单条消息：message.process 上下文保证退出时自动 ack。

    处理过程中抛异常会自动 nack（MVP 不重入队，后续配合死信队列处理）。
    """
    async with message.process():
        payload = json.loads(message.body.decode("utf-8"))
        logger.info(
            "order_paid_event_received order_id=%s user_id=%s event=%s",
            payload.get("order_id"),
            payload.get("user_id"),
            payload.get("event"),
        )


async def consume_order_paid_event() -> None:
    """声明 queue + 绑定 exchange + 注册消费者，然后持续运行。

    被 asyncio.Task 包裹运行；任务被 cancel（应用关闭）时退出。
    """
    channel = get_channel()
    if channel is None:
        raise RuntimeError("RabbitMQ 未连接，无法启动 order.paid 消费者")

    # 声明队列并绑定到 topic exchange（durable，与 publisher 对齐）
    queue = await channel.declare_queue(ORDER_PAID_QUEUE, durable=True)
    await queue.bind(ORDER_EVENTS_EXCHANGE, routing_key=ORDER_PAID_ROUTING_KEY)

    await queue.consume(_on_message)
    logger.info(
        "order_paid_consumer_started queue=%s routing_key=%s",
        ORDER_PAID_QUEUE,
        ORDER_PAID_ROUTING_KEY,
    )

    # 保持任务存活：consume 回调已注册到事件循环，这里等待被 cancel
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("order_paid_consumer_stopped")
        raise
