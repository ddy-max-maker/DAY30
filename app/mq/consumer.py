"""消息消费者：监听 order_paid_queue 并处理 order.paid 事件。

可靠消费策略（Step 4A）：
- 业务处理成功 → ACK 原消息
- 业务处理失败且 retry_count < MAX_RETRIES → 发布到 retry queue → ACK 原消息
- 业务处理失败且 retry_count >= MAX_RETRIES → 发布到 DLQ → ACK 原消息
- retry / DLQ 发布本身失败 → 不 ACK 原消息，nack(requeue=True) 作为基础设施兜底

拓扑：
  主队列 order_paid_queue (durable)
       ↑ bind order_events / order.paid

  重试队列 order_paid_retry_queue (durable, TTL=5s, DLX→order_events/order.paid)
       消息进入后等待 5 秒 → TTL 到期 → 自动路由回主队列

  死信交换机 order_events_dlx (topic, durable)
       ↓ bind order.paid.dead
  死信队列 order_paid_dlq (durable)

重试计数通过消息 header x-retry-count 跟踪，初始为 0。
"""

import asyncio
import json
import logging
from typing import Literal

import aio_pika

from app.mq.publisher import ORDER_PAID_ROUTING_KEY
from app.mq.rabbitmq import (
    MAX_RETRIES,
    ORDER_EVENTS_DLX,
    ORDER_EVENTS_EXCHANGE,
    ORDER_PAID_DEAD_ROUTING_KEY,
    ORDER_PAID_DLQ,
    ORDER_PAID_QUEUE,
    ORDER_PAID_RETRY_QUEUE,
    RETRY_TTL_MS,
    X_RETRY_COUNT,
    get_channel,
    get_order_events_dlx,
)

logger = logging.getLogger("backend")

# 纯逻辑动作类型：便于单测，不依赖 aio-pika mock
Action = Literal["ack", "retry", "dead_letter", "nack_requeue"]


# ================ 纯逻辑：决策下一步动作 ================
def decide_action(retry_count: int, max_retries: int = MAX_RETRIES) -> Action:
    """根据重试次数决定下一步动作（纯函数，方便单测）。

    - retry_count < max_retries → "retry"（发布到 retry queue）
    - retry_count >= max_retries → "dead_letter"（发布到 DLQ）
    """
    if retry_count < max_retries:
        return "retry"
    return "dead_letter"


# ================ 纯逻辑：读取重试计数 ================
def get_retry_count(message_headers: dict | None) -> int:
    """从消息 header 读取 x-retry-count，无此字段视为 0（纯函数）。"""
    if not message_headers:
        return 0
    val = message_headers.get(X_RETRY_COUNT, 0)
    return int(val) if isinstance(val, (int, float)) else 0


# ================ 纯逻辑：构造重试消息 ================
def build_retry_message(
    original: aio_pika.Message, new_retry_count: int
) -> aio_pika.Message:
    """基于原消息构造重试消息：保留 body / content_type / delivery_mode，
    只更新 x-retry-count header。

    纯函数（不涉及网络 I/O），方便单测。
    """
    headers = dict(original.headers) if original.headers else {}
    headers[X_RETRY_COUNT] = new_retry_count

    return aio_pika.Message(
        body=original.body,
        content_type=original.content_type,
        delivery_mode=original.delivery_mode,
        correlation_id=original.correlation_id,
        headers=headers,
    )


# ================ 纯逻辑：构造死信消息 ================
def build_dead_letter_message(
    original: aio_pika.Message, retry_count: int
) -> aio_pika.Message:
    """基于原消息构造死信消息：保留 body / content_type / delivery_mode，
    更新 x-retry-count 为最终值。"""
    headers = dict(original.headers) if original.headers else {}
    headers[X_RETRY_COUNT] = retry_count

    return aio_pika.Message(
        body=original.body,
        content_type=original.content_type,
        delivery_mode=original.delivery_mode,
        correlation_id=original.correlation_id,
        headers=headers,
    )


# ================ 业务处理 ================
async def _handle_order_paid(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    """处理 order.paid 消息：解析 JSON 并打印结构化日志。

    MVP 阶段仅记录日志。后续可在此处接入库存、积分等下游逻辑。
    业务处理失败抛异常会触发 retry / DLQ 流程。
    """
    payload = json.loads(message.body.decode("utf-8"))
    logger.info(
        "order_paid_event_processed order_id=%s user_id=%s event=%s",
        payload.get("order_id"),
        payload.get("user_id"),
        payload.get("event"),
    )


# ================ 发布重试消息 ================
async def _publish_retry_message(
    original: aio_pika.Message, new_retry_count: int
) -> bool:
    """发布重试消息到 order_paid_retry_queue。

    通过 default_exchange + routing_key=queue_name 直接投递到队列。
    消息在 retry queue 中等待 RETRY_TTL_MS 后由 DLX 自动路由回主队列。

    返回 True 表示 Broker 已确认（publisher confirm）。
    """
    channel = get_channel()
    if channel is None:
        raise RuntimeError("RabbitMQ 未连接，无法发布重试消息")

    retry_msg = build_retry_message(original, new_retry_count)
    # default_exchange + routing_key=queue_name = 直接投递到指定队列
    confirmed = await channel.default_exchange.publish(
        retry_msg, routing_key=ORDER_PAID_RETRY_QUEUE
    )
    return confirmed


# ================ 发布死信消息 ================
async def _publish_dead_letter_message(
    original: aio_pika.Message, retry_count: int
) -> bool:
    """发布死信消息到 order_events_dlx（routing_key=order.paid.dead），
    由 DLX 路由到 order_paid_dlq。

    返回 True 表示 Broker 已确认（publisher confirm）。
    """
    dlx = get_order_events_dlx()
    if dlx is None:
        raise RuntimeError("RabbitMQ 未连接，无法发布死信消息")

    dead_msg = build_dead_letter_message(original, retry_count)
    confirmed = await dlx.publish(dead_msg, routing_key=ORDER_PAID_DEAD_ROUTING_KEY)
    return confirmed


# ================ 主回调 ================
async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    """处理单条消息的完整回调：ACK / retry / DLQ / nack 兜底。

    关键：不使用 message.process() 自动 ack，改为手动控制。
    retry / DLQ 发布成功后才 ACK 原消息；发布失败则 nack(requeue=True)。
    """
    retry_count = get_retry_count(message.headers)

    try:
        await _handle_order_paid(message)
        # 业务成功 → ACK
        await message.ack()
        logger.info(
            "order_paid_event_acked order_id_header=%s retry_count=%s",
            retry_count,
            retry_count,
        )
        return
    except Exception:
        # 业务失败，logger 会记录 traceback（下方 retry / DLQ 分支处理）
        pass

    action = decide_action(retry_count)

    if action == "retry":
        new_count = retry_count + 1
        logger.warning(
            "order_paid_event_retry retry_count=%s max_retries=%s",
            new_count,
            MAX_RETRIES,
        )
        try:
            confirmed = await _publish_retry_message(message, new_count)
            if not confirmed:
                raise RuntimeError("Broker 未确认重试消息（publisher confirm=False）")
            # 重试消息发布成功 → ACK 原消息
            await message.ack()
        except Exception:
            logger.error(
                "retry_publish_failed retry_count=%s",
                new_count,
                exc_info=True,
            )
            # 基础设施故障兜底：nack 原消息回主队列（避免消息丢失）
            await message.nack(requeue=True)

    elif action == "dead_letter":
        logger.error(
            "order_paid_event_dead_lettered retry_count=%s max_retries=%s",
            retry_count,
            MAX_RETRIES,
        )
        try:
            confirmed = await _publish_dead_letter_message(message, retry_count)
            if not confirmed:
                raise RuntimeError("Broker 未确认死信消息（publisher confirm=False）")
            # 死信发布成功 → ACK 原消息
            await message.ack()
        except Exception:
            logger.error(
                "dlq_publish_failed retry_count=%s",
                retry_count,
                exc_info=True,
            )
            # 基础设施故障兜底：nack 原消息回主队列
            await message.nack(requeue=True)


# ================ 队列声明 + 消费者启动 ================
async def consume_order_paid_event() -> None:
    """声明所有队列 + 绑定 + 注册消费者，然后持续运行。

    队列拓扑：
    - order_paid_queue: 主队列，绑定 order_events / order.paid
    - order_paid_retry_queue: 重试队列，TTL=5s，DLX→order_events/order.paid
    - order_paid_dlq: 死信队列，绑定 order_events_dlx / order.paid.dead
    """
    channel = get_channel()
    if channel is None:
        raise RuntimeError("RabbitMQ 未连接，无法启动 order.paid 消费者")

    # --- 主队列 ---
    main_queue = await channel.declare_queue(ORDER_PAID_QUEUE, durable=True)
    await main_queue.bind(ORDER_EVENTS_EXCHANGE, routing_key=ORDER_PAID_ROUTING_KEY)

    # --- 重试队列：TTL 到期后自动路由回主 exchange ---
    # 声明但不赋值给变量——retry queue 不需要消费者，
    # 消息只在里面"坐等" TTL 到期后由 DLX 自动路由回主队列
    await channel.declare_queue(
        ORDER_PAID_RETRY_QUEUE,
        durable=True,
        arguments={
            "x-message-ttl": RETRY_TTL_MS,
            "x-dead-letter-exchange": ORDER_EVENTS_EXCHANGE,
            "x-dead-letter-routing-key": ORDER_PAID_ROUTING_KEY,
        },
    )

    # --- 死信队列 ---
    dlq = await channel.declare_queue(ORDER_PAID_DLQ, durable=True)
    await dlq.bind(ORDER_EVENTS_DLX, routing_key=ORDER_PAID_DEAD_ROUTING_KEY)

    # --- 注册消费者（不使用 auto_ack，手动控制 ack/nack）---
    await main_queue.consume(_on_message, no_ack=False)
    logger.info(
        "order_paid_consumer_started queue=%s retry_queue=%s dlq=%s",
        ORDER_PAID_QUEUE,
        ORDER_PAID_RETRY_QUEUE,
        ORDER_PAID_DLQ,
    )

    # 保持任务存活：consume 回调已注册到事件循环，这里等待被 cancel
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info("order_paid_consumer_stopped")
        raise
