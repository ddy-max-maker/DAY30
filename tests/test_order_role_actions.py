"""订单角色动作 API 测试：CUSTOMER / MERCHANT / ADMIN 三维权限。

覆盖：
1. CUSTOMER 确认收货成功（SHIPPED → COMPLETED）
2. CUSTOMER 调商家履约接口 → 403（接口要求 MERCHANT 角色）
3. MERCHANT 履约自己的订单成功（prepare/ready/ship 全链）
4. MERCHANT 不能确认收货 → 403
5. MERCHANT 操作其他商家的订单 → 404（不泄露存在性）
6. ADMIN 调履约接口 → 403（ADMIN 只读）；万能跳状态接口已删除 → 404
"""

from app.core.config import settings
from app.core.security import create_access_token
from tests.conftest import TestingSessionLocal
from tests.factories.product_factory import create_test_product
from tests.factories.sku_factory import create_test_sku
from tests.factories.user_factory import create_test_merchant, create_test_user


def _setup():
    """商家 A 的商品 + 消费者 + 商家 B。返回普通 ID（避免 ORM 对象脱管）。"""
    db = TestingSessionLocal()
    merchant_a = create_test_merchant(db, email="ra-merchant-a@example.com")
    merchant_b = create_test_merchant(db, email="ra-merchant-b@example.com")
    product = create_test_product(db, merchant_id=merchant_a.id, name="RoleP")
    sku = create_test_sku(
        db, product_id=product.id, sku_code="ROLE-SKU", price="10.00", stock=10
    )
    user = create_test_user(db, email="ra-user@example.com")
    merchant_a_id, merchant_b_id, sku_id, user_id = (
        merchant_a.id,
        merchant_b.id,
        sku.id,
        user.id,
    )
    db.close()
    return merchant_a_id, merchant_b_id, sku_id, user_id


def _headers(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _paid_order(client, sku_id: int, user_id: int, reference="PAY-ROLE-0001") -> int:
    """创建并支付一笔订单，返回 order_id。"""
    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=_headers(user_id),
    )
    assert resp.status_code == 200
    order_id = resp.json()["data"]["id"]

    callback = client.post(
        "/payments/callback",
        json={
            "order_id": order_id,
            "payment_reference": reference,
            "status": "success",
        },
        headers={"X-Mock-Payment-Secret": settings.MOCK_PAYMENT_SECRET},
    )
    assert callback.status_code == 200
    return order_id


# ================ 1. CUSTOMER 确认收货 ================
def test_customer_confirm_delivered_order(client):
    """SHIPPED 订单客户确认收货 → COMPLETED。"""
    merchant_a_id, _, sku_id, user_id = _setup()
    order_id = _paid_order(client, sku_id, user_id)

    # 商家履约到 SHIPPED
    for action in ("prepare", "ready", "ship"):
        resp = client.post(
            f"/merchant/orders/{order_id}/{action}", headers=_headers(merchant_a_id)
        )
        assert resp.status_code == 200

    resp = client.post(f"/orders/{order_id}/confirm", headers=_headers(user_id))
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "completed"


# ================ 2. CUSTOMER 不能执行商家动作 ================
def test_customer_cannot_call_merchant_actions(client):
    """CUSTOMER 调 /merchant/orders/{id}/prepare → 403（接口要求 MERCHANT 角色）。"""
    _, _, sku_id, user_id = _setup()
    order_id = _paid_order(client, sku_id, user_id)

    resp = client.post(
        f"/merchant/orders/{order_id}/prepare", headers=_headers(user_id)
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 10001  # PermissionDeniedError


# ================ 3. MERCHANT 不能确认收货 ================
def test_merchant_cannot_confirm_delivery(client):
    """MERCHANT 调 /orders/{id}/confirm → 403（接口要求 CUSTOMER 角色）。"""
    merchant_a_id, _, sku_id, user_id = _setup()
    order_id = _paid_order(client, sku_id, user_id)

    resp = client.post(f"/orders/{order_id}/confirm", headers=_headers(merchant_a_id))
    assert resp.status_code == 403
    assert resp.json()["code"] == 10001


def test_cancelled_order_cannot_be_confirmed(client):
    """已取消订单不能确认收货 → 409（动作合法但状态不允许）。"""
    _, _, sku_id, user_id = _setup()

    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=_headers(user_id),
    )
    order_id = resp.json()["data"]["id"]

    cancel = client.post(f"/orders/{order_id}/cancel", headers=_headers(user_id))
    assert cancel.status_code == 200

    resp = client.post(f"/orders/{order_id}/confirm", headers=_headers(user_id))
    assert resp.status_code == 409
    assert resp.json()["code"] == 10105  # OrderStatusError


# ================ 4. MERCHANT 归属校验 ================
def test_merchant_cannot_fulfill_other_merchant_order(client):
    """商家 B 操作商家 A 的订单 → 404（不泄露存在性）。"""
    merchant_a_id, merchant_b_id, sku_id, user_id = _setup()
    order_id = _paid_order(client, sku_id, user_id)

    resp = client.post(
        f"/merchant/orders/{order_id}/prepare", headers=_headers(merchant_b_id)
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == 10104  # OrderNotFoundError


# ================ 5. ADMIN 不能执行任何订单动作 ================
def test_admin_cannot_fulfill_or_modify(client):
    """ADMIN 调商家履约接口 → 403；万能跳状态接口已删除 → 404。"""
    from tests.factories.user_factory import create_test_admin

    db = TestingSessionLocal()
    admin = create_test_admin(db, email="ra-admin@example.com")
    admin_id = admin.id
    db.close()

    merchant_a_id, _, sku_id, user_id = _setup()
    order_id = _paid_order(client, sku_id, user_id)

    resp = client.post(
        f"/merchant/orders/{order_id}/prepare", headers=_headers(admin_id)
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 10001

    resp = client.patch(
        f"/admin/orders/{order_id}/status",
        json={"status": "shipped"},
        headers=_headers(admin_id),
    )
    assert resp.status_code == 404
