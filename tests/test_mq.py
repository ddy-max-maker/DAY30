"""RabbitMQ 订单支付事件测试。

不强依赖真实 RabbitMQ：
- conftest 已设置 RABBITMQ_ENABLED=false，lifespan 跳过 MQ 连接
- 支付事件通过 monkeypatch mock publisher 验证调用
- 消息构造 / routing_key / 未连接异常走纯单元测试

覆盖：
1. RabbitMQ 配置读取正常
2. publisher 构造的消息体正确
3. routing_key 正确
4. 支付接口调用后触发 publish（order_id / user_id 正确）
5. MQ 发送失败不影响支付结果（订单仍为 paid，接口 200）
6. 未连接时真实 publisher 抛异常（由调用方捕获记日志）
"""

import pytest

from app.core.config import settings
from app.mq import rabbitmq
from app.mq.publisher import (
    ORDER_PAID_ROUTING_KEY,
    build_order_paid_message,
    publish_order_paid_event,
)
from app.services import order_service


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_sku(client, admin_token, sku_code="MQ-SKU"):
    """通过管理员 API 创建商品 + SKU，返回 sku_id。"""
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


# ================ 1. 配置读取 ================
def test_rabbitmq_config_loaded():
    """RabbitMQ 配置项存在且类型正确；测试环境禁用真实连接。"""
    assert isinstance(settings.RABBITMQ_HOST, str)
    assert isinstance(settings.RABBITMQ_PORT, int)
    assert isinstance(settings.RABBITMQ_USER, str)
    assert isinstance(settings.RABBITMQ_PASSWORD, str)
    assert settings.RABBITMQ_ENABLED is False  # conftest 设置


def test_amqp_url_contains_credentials():
    """AMQP URL 正确拼装 host/port（guest 默认凭据可被 URL 编码）。"""
    url = rabbitmq.build_amqp_url()
    assert url.startswith("amqp://")
    assert f"{settings.RABBITMQ_HOST}:{settings.RABBITMQ_PORT}" in url


# ================ 2. 消息构造 ================
def test_build_order_paid_message():
    """消息体字段明确，不包含 ORM 对象。"""
    msg = build_order_paid_message(order_id=1001, user_id=8)
    assert msg == {
        "event": "order.paid",
        "order_id": 1001,
        "user_id": 8,
    }


# ================ 3. routing_key ================
def test_routing_key_is_order_paid():
    """routing_key 与事件名一致，绑定 order_paid_queue。"""
    assert ORDER_PAID_ROUTING_KEY == "order.paid"
    assert rabbitmq.ORDER_EVENTS_EXCHANGE == "order_events"
    assert rabbitmq.ORDER_PAID_QUEUE == "order_paid_queue"


# ================ 4. 支付接口触发 publish ================
def test_pay_endpoint_publishes_event(client, user_token, admin_token, monkeypatch):
    """POST /orders/{id}/pay 成功后，publisher 以正确参数被调用一次。"""
    sku_id = _create_sku(client, admin_token, sku_code="MQ-PUB-1")
    order_id = _create_pending_order(client, user_token, sku_id)

    # 从 JWT 解析出当前用户 id：下单响应里有 user_id，这里直接查订单详情
    order_detail = client.get(f"/orders/{order_id}", headers=_auth(user_token)).json()[
        "data"
    ]
    user_id = order_detail["user_id"]

    published: list[dict] = []

    async def fake_publish(pub_order_id, pub_user_id):
        published.append({"order_id": pub_order_id, "user_id": pub_user_id})

    # order_service 通过 from ... import 引入，必须 patch 其模块内引用
    monkeypatch.setattr(order_service, "publish_order_paid_event", fake_publish)

    response = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "paid"

    assert len(published) == 1
    assert published[0] == {"order_id": order_id, "user_id": user_id}


# ================ 5. MQ 发送失败不影响支付 ================
def test_publish_failure_does_not_break_payment(
    client, user_token, admin_token, monkeypatch
):
    """publish 抛异常时，支付仍成功返回 paid（异常在 service 层被捕获记日志）。"""
    sku_id = _create_sku(client, admin_token, sku_code="MQ-PUB-2")
    order_id = _create_pending_order(client, user_token, sku_id)

    async def broken_publish(pub_order_id, pub_user_id):
        raise RuntimeError("RabbitMQ 不可用")

    monkeypatch.setattr(order_service, "publish_order_paid_event", broken_publish)

    response = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "paid"

    # 状态已持久化（重复支付返回 409，证明上一次确实提交为 PAID）
    repeat = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert repeat.status_code == 409


# ================ 6. 未连接时真实 publisher 抛异常 ================
def test_publisher_raises_when_not_connected():
    """RABBITMQ_ENABLED=false 时 exchange 为 None，publish 必须抛出而非静默成功。

    用 asyncio.run 驱动协程，避免引入 pytest-asyncio 新依赖。
    """
    import asyncio

    assert rabbitmq.get_order_events_exchange() is None
    with pytest.raises(RuntimeError, match="RabbitMQ 未连接"):
        asyncio.run(publish_order_paid_event(order_id=1, user_id=1))
