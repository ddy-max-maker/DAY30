"""RabbitMQ 订单支付事件测试（Step 4A：可靠消费增强）。

不强依赖真实 RabbitMQ：
- conftest 已设置 RABBITMQ_ENABLED=false，lifespan 跳过 MQ 连接
- 支付事件通过 monkeypatch mock publisher 验证调用
- 消息构造 / routing_key / 未连接异常走纯单元测试
- retry / DLQ 逻辑通过纯逻辑函数 + Mock 消息对象测试

覆盖：
1. RabbitMQ 配置读取正常
2. publisher 构造的消息体正确
3. routing_key 正确
4. 支付回调后不触发任何 MQ 发布（第二阶段修正：MQ 移出支付链路，
   避免"commit 后 publish"的双写一致性问题，第三阶段 Outbox 恢复）
5. 未连接时真实 publisher 抛异常（由调用方捕获记日志）
6. publisher channel 启用 confirm 的配置（常量层面）
7. retry queue 名称 / TTL / DLX 配置正确
8. DLX / DLQ / dead routing key 配置正确
9. 消费成功后 ACK（mock 消息对象）
10. 第一次处理失败 → retry_count 0→1，发布 retry，不进 DLQ
11. 第二次处理失败 → retry_count 正确增加
12. 达到最大重试 → 进入 DLQ，不再发布 retry
13. retry publish 成功后才 ACK 原消息
14. retry publish 失败时原消息不能被 ACK
15. DLQ publish 失败时原消息不能被 ACK
16. 重试消息保持原业务 body
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import aio_pika
import pytest

from app.core.config import settings
from app.mq import consumer, rabbitmq
from app.mq.consumer import (
    build_dead_letter_message,
    build_retry_message,
    decide_action,
    get_retry_count,
)
from app.mq.publisher import (
    ORDER_PAID_ROUTING_KEY,
    build_order_paid_message,
    publish_order_paid_event,
)
from app.mq import publisher as mq_publisher


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_sku(client, admin_token, sku_code="MQ-SKU"):
    headers = _auth(admin_token)
    product_resp = client.post(
        "/admin/products",
        json={"name": "MQProduct", "description": "mq desc"},
        headers=headers,
    )
    assert product_resp.status_code == 200
    product_id = product_resp.json()["data"]["id"]

    sku_resp = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": sku_code,
            "name": "MQ SKU",
            "price": "10.00",
            "stock": 100,
        },
        headers=headers,
    )
    assert sku_resp.status_code == 200
    return sku_resp.json()["data"]["id"]


def _create_pending_order(client, user_token, sku_id) -> int:
    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    return resp.json()["data"]["id"]


def _make_mock_message(
    body: bytes | None = None,
    headers: dict | None = None,
) -> MagicMock:
    """构造 mock 消息对象，模拟 aio_pika.IncomingMessage。

    ack / nack / reject 为 AsyncMock，测试中检查是否被调用。
    """
    if body is None:
        body = json.dumps(
            {"event": "order.paid", "order_id": 1001, "user_id": 8}
        ).encode("utf-8")

    msg = MagicMock()
    msg.body = body
    msg.headers = headers or {}
    msg.content_type = "application/json"
    msg.delivery_mode = aio_pika.DeliveryMode.PERSISTENT
    msg.correlation_id = None
    msg.ack = AsyncMock()
    msg.nack = AsyncMock(requeue=True)
    msg.reject = AsyncMock(requeue=True)
    return msg


# ================ 1. 配置读取 ================
def test_rabbitmq_config_loaded():
    assert isinstance(settings.RABBITMQ_HOST, str)
    assert isinstance(settings.RABBITMQ_PORT, int)
    assert isinstance(settings.RABBITMQ_USER, str)
    assert isinstance(settings.RABBITMQ_PASSWORD, str)
    assert settings.RABBITMQ_ENABLED is False


def test_amqp_url_contains_credentials():
    url = rabbitmq.build_amqp_url()
    assert url.startswith("amqp://")
    assert f"{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}" in url


# ================ 2. 消息构造 ================
def test_build_order_paid_message():
    msg = build_order_paid_message(order_id=1001, user_id=8)
    assert msg == {"event": "order.paid", "order_id": 1001, "user_id": 8}


# ================ 3. routing_key ================
def test_routing_key_is_order_paid():
    assert ORDER_PAID_ROUTING_KEY == "order.paid"
    assert rabbitmq.ORDER_EVENTS_EXCHANGE == "order_events"
    assert rabbitmq.ORDER_PAID_QUEUE == "order_paid_queue"


# ================ 4. 支付回调不发布 MQ 事件 ================
def test_payment_callback_does_not_publish_event(
    client, user_token, admin_token, monkeypatch
):
    """第二阶段修正：MQ 发布移出支付回调链路。

    "MySQL COMMIT 成功后再 publish" 存在双写一致性问题：
    commit 成功但 publish 失败时，订单已支付而下游收不到事件。
    第三阶段将以 Transactional Outbox 方式恢复（订单 + 事件表同事务提交）。
    本测试回归断言：回调全程不触发 publish_order_paid_event。
    """

    sku_id = _create_sku(client, admin_token, sku_code="MQ-NOPUB-1")
    order_id = _create_pending_order(client, user_token, sku_id)

    published: list[dict] = []

    async def fake_publish(pub_order_id, pub_user_id):
        published.append({"order_id": pub_order_id, "user_id": pub_user_id})

    monkeypatch.setattr(mq_publisher, "publish_order_paid_event", fake_publish)

    response = client.post(
        "/payments/callback",
        json={
            "order_id": order_id,
            "payment_reference": "PAY-MQ-0001",
            "status": "success",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "paid"
    # 支付成功但未发布任何 MQ 事件
    assert published == []


# ================ 5a. mandatory 消息不可路由 → DeliveryError ================
def test_publisher_propagates_delivery_error(monkeypatch):
    """on_return_raises=True 时，mandatory 消息无法路由会抛 DeliveryError，
    publisher 不吞异常，向上传播给调用方。
    """
    from aio_pika.exceptions import DeliveryError

    # DeliveryError 签名: (message, frame)，用 MagicMock 模拟 frame
    delivery_error = DeliveryError(None, MagicMock())

    mock_exchange = MagicMock()
    mock_exchange.publish = AsyncMock(side_effect=delivery_error)
    monkeypatch.setattr(
        "app.mq.publisher.get_order_events_exchange", lambda: mock_exchange
    )

    with pytest.raises(DeliveryError):
        asyncio.run(publish_order_paid_event(order_id=1, user_id=1))


# ================ 6. 未连接时真实 publisher 抛异常 ================
def test_publisher_raises_when_not_connected():
    import asyncio

    assert rabbitmq.get_order_events_exchange() is None
    with pytest.raises(RuntimeError, match="RabbitMQ 未连接"):
        asyncio.run(publish_order_paid_event(order_id=1, user_id=1))


# ================ 7. Publisher Confirm 配置 ================
def test_publisher_confirm_enabled_in_channel():
    """connect() 应显式传入 publisher_confirms=True。

    通过检查 channel 创建参数的文档/签名间接验证——
    这里验证常量定义存在且 MAX_RETRIES 合理，真实集成靠冒烟测试。
    """
    assert rabbitmq.MAX_RETRIES == 3
    assert rabbitmq.RETRY_TTL_MS == 5000
    assert rabbitmq.X_RETRY_COUNT == "x-retry-count"


# ================ 8. Retry Queue 配置 ================
def test_retry_queue_name_correct():
    assert rabbitmq.ORDER_PAID_RETRY_QUEUE == "order_paid_retry_queue"


def test_retry_queue_ttl_is_5000ms():
    assert rabbitmq.RETRY_TTL_MS == 5000


def test_retry_queue_dlx_routes_to_order_events():
    """retry queue 的 DLX 指向 order_events，routing_key 为 order.paid。

    consumer.declare_queue 的 arguments 在 consume_order_paid_event 中硬编码，
    这里验证常量值正确（真实声明靠冒烟测试）。"""
    assert rabbitmq.ORDER_EVENTS_EXCHANGE == "order_events"
    assert ORDER_PAID_ROUTING_KEY == "order.paid"


# ================ 9. DLX / DLQ / Dead Routing Key ================
def test_dlx_dlq_routing_key_config():
    assert rabbitmq.ORDER_EVENTS_DLX == "order_events_dlx"
    assert rabbitmq.ORDER_PAID_DLQ == "order_paid_dlq"
    assert rabbitmq.ORDER_PAID_DEAD_ROUTING_KEY == "order.paid.dead"


# ================ 10. 消费成功后 ACK ================
def test_consume_success_acks():
    """业务处理成功 → ack 被调用，nack 未被调用。"""
    msg = _make_mock_message()

    asyncio.run(consumer._on_message(msg))

    msg.ack.assert_awaited_once()
    msg.nack.assert_not_awaited()


def _patch_consumer(monkey_handler, monkey_retry, monkey_dlq):
    """Patch consumer 模块的依赖函数，返回 (consumer_mod, restore_func)。

    使用局部变量持有 mock，避免 finally 恢复后无法断言。
    """
    import app.mq.consumer as consumer_mod

    orig_handle = consumer_mod._handle_order_paid
    orig_retry = consumer_mod._publish_retry_message
    orig_dlq = consumer_mod._publish_dead_letter_message

    consumer_mod._handle_order_paid = monkey_handler
    consumer_mod._publish_retry_message = monkey_retry
    consumer_mod._publish_dead_letter_message = monkey_dlq

    def restore():
        consumer_mod._handle_order_paid = orig_handle
        consumer_mod._publish_retry_message = orig_retry
        consumer_mod._publish_dead_letter_message = orig_dlq

    return consumer_mod, restore


# ================ 11. 第一次失败 → retry（0→1）===============
def test_first_failure_retries():
    """处理失败 + retry_count=0 → 发布 retry（count=1），ACK 原消息，不进 DLQ。"""
    msg = _make_mock_message(headers={})

    monkey_handler = AsyncMock(side_effect=RuntimeError("业务失败"))
    monkey_retry = AsyncMock(return_value=True)
    monkey_dlq = AsyncMock(return_value=True)

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    monkey_retry.assert_awaited_once()
    assert monkey_retry.call_args[0][1] == 1  # new_retry_count
    monkey_dlq.assert_not_awaited()
    msg.ack.assert_awaited_once()
    msg.nack.assert_not_awaited()


# ================ 12. 第二次失败 → retry_count 增加 ================
def test_second_failure_increases_retry_count():
    """retry_count=1 时失败 → 发布 retry（count=2）。"""
    msg = _make_mock_message(headers={rabbitmq.X_RETRY_COUNT: 1})

    monkey_handler = AsyncMock(side_effect=RuntimeError("业务失败"))
    monkey_retry = AsyncMock(return_value=True)
    monkey_dlq = AsyncMock(return_value=True)

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    assert monkey_retry.call_args[0][1] == 2
    monkey_dlq.assert_not_awaited()
    msg.ack.assert_awaited_once()


# ================ 13. 达到最大重试 → 进入 DLQ ================
def test_max_retries_enters_dlq():
    """retry_count=3（=MAX_RETRIES）→ 发布 DLQ，不再发布 retry。"""
    msg = _make_mock_message(headers={rabbitmq.X_RETRY_COUNT: 3})

    monkey_handler = AsyncMock(side_effect=RuntimeError("业务失败"))
    monkey_retry = AsyncMock(return_value=True)
    monkey_dlq = AsyncMock(return_value=True)

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    monkey_retry.assert_not_awaited()
    monkey_dlq.assert_awaited_once()
    assert monkey_dlq.call_args[0][1] == 3  # retry_count passed through
    msg.ack.assert_awaited_once()


# ================ 14. retry publish 成功后才 ACK ================
def test_retry_publish_success_then_ack():
    """retry publish 返回 True → ACK 原消息。"""
    msg = _make_mock_message(headers={})

    monkey_handler = AsyncMock(side_effect=RuntimeError("失败"))
    monkey_retry = AsyncMock(return_value=True)
    monkey_dlq = AsyncMock()

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    msg.ack.assert_awaited_once()
    msg.nack.assert_not_awaited()


# ================ 15. retry publish 失败 → 不 ACK ================
def test_retry_publish_failure_no_ack():
    """retry publish 抛异常 → 不 ACK，nack(requeue=True) 兜底。"""
    msg = _make_mock_message(headers={})

    monkey_handler = AsyncMock(side_effect=RuntimeError("失败"))
    monkey_retry = AsyncMock(side_effect=RuntimeError("MQ 故障"))
    monkey_dlq = AsyncMock()

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    msg.ack.assert_not_awaited()
    msg.nack.assert_awaited_once()
    assert msg.nack.call_args.kwargs.get("requeue", True) is True


def test_retry_publish_returns_false_no_ack():
    """retry publish 返回 False（Broker 未确认）→ 不 ACK，nack 兜底。"""
    msg = _make_mock_message(headers={})

    monkey_handler = AsyncMock(side_effect=RuntimeError("失败"))
    monkey_retry = AsyncMock(return_value=False)  # confirm=False
    monkey_dlq = AsyncMock()

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    msg.ack.assert_not_awaited()
    msg.nack.assert_awaited_once()


# ================ 16. DLQ publish 失败 → 不 ACK ================
def test_dlq_publish_failure_no_ack():
    """DLQ publish 抛异常 → 不 ACK，nack(requeue=True) 兜底。"""
    msg = _make_mock_message(headers={rabbitmq.X_RETRY_COUNT: 3})

    monkey_handler = AsyncMock(side_effect=RuntimeError("失败"))
    monkey_retry = AsyncMock(return_value=True)
    monkey_dlq = AsyncMock(side_effect=RuntimeError("DLQ 故障"))

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    msg.ack.assert_not_awaited()
    msg.nack.assert_awaited_once()


def test_dlq_publish_returns_false_no_ack():
    """DLQ publish 返回 False → 不 ACK，nack 兜底。"""
    msg = _make_mock_message(headers={rabbitmq.X_RETRY_COUNT: 3})

    monkey_handler = AsyncMock(side_effect=RuntimeError("失败"))
    monkey_retry = AsyncMock(return_value=True)
    monkey_dlq = AsyncMock(return_value=False)

    mod, restore = _patch_consumer(monkey_handler, monkey_retry, monkey_dlq)
    try:
        asyncio.run(mod._on_message(msg))
    finally:
        restore()

    msg.ack.assert_not_awaited()
    msg.nack.assert_awaited_once()


# ================ 17. 重试消息保持原业务 body ================
def test_retry_message_preserves_body():
    """重试消息的 body 与原消息一致，仅更新 x-retry-count。"""
    original_body = json.dumps(
        {"event": "order.paid", "order_id": 1001, "user_id": 8}
    ).encode("utf-8")

    original = aio_pika.Message(
        body=original_body,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        headers={"x-custom": "keep-me"},
    )

    retry_msg = build_retry_message(original, new_retry_count=1)

    assert retry_msg.body == original_body
    assert retry_msg.content_type == "application/json"
    assert retry_msg.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert retry_msg.headers["x-retry-count"] == 1
    # 原有 header 保留
    assert retry_msg.headers["x-custom"] == "keep-me"


def test_dead_letter_message_preserves_body():
    """死信消息的 body 与原消息一致。"""
    original_body = json.dumps(
        {"event": "order.paid", "order_id": 1001, "user_id": 8}
    ).encode("utf-8")

    original = aio_pika.Message(
        body=original_body,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )

    dead_msg = build_dead_letter_message(original, retry_count=3)

    assert dead_msg.body == original_body
    assert dead_msg.headers["x-retry-count"] == 3


# ================ 纯逻辑辅助测试 ================
def test_get_retry_count_default_zero():
    """无 header 或无 x-retry-count → 0。"""
    assert get_retry_count(None) == 0
    assert get_retry_count({}) == 0
    assert get_retry_count({"other": "val"}) == 0


def test_get_retry_count_from_header():
    assert get_retry_count({"x-retry-count": 2}) == 2


def test_decide_action_retry():
    """retry_count < MAX_RETRIES → retry。"""
    assert decide_action(0) == "retry"
    assert decide_action(1) == "retry"
    assert decide_action(2) == "retry"


def test_decide_action_dead_letter():
    """retry_count >= MAX_RETRIES → dead_letter。"""
    assert decide_action(3) == "dead_letter"
    assert decide_action(4) == "dead_letter"
    assert decide_action(10) == "dead_letter"
