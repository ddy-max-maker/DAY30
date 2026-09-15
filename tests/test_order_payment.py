"""支付流程测试：模拟用户支付订单（PENDING → PAID）。

使用 factory + db_session 直接写库准备 SKU 数据，
通过 API 验证支付行为，确保测试隔离。

覆盖：
1. 用户支付自己的 PENDING 订单成功
2. 支付后状态变为 PAID
3. 支付不存在的订单返回 404
4. 用户不能支付其他人的订单（404）
5. 重复支付失败（PAID → PAID，返回 409）
"""

from app.core.security import create_access_token
from tests.factories.user_factory import create_test_user


def _auth(token: str) -> dict:
    """把 token 字符串包装成 Authorization header。"""
    return {"Authorization": f"Bearer {token}"}


def _create_sku(
    client,
    admin_token,
    name="P",
    sku_code="PAY-SKU",
    price="10.00",
    stock=100,
):
    """通过 API 创建商品 + SKU，返回 sku_id。管理员接口，需要 admin_token。"""
    headers = _auth(admin_token)

    product_resp = client.post(
        "/admin/products",
        json={"name": name, "description": f"{name} desc"},
        headers=headers,
    )
    assert product_resp.status_code == 200
    product_id = product_resp.json()["data"]["id"]

    sku_resp = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": sku_code,
            "name": f"{name} SKU",
            "price": price,
            "stock": stock,
        },
        headers=headers,
    )
    assert sku_resp.status_code == 200
    return sku_resp.json()["data"]["id"]


def _create_order(client, user_token, sku_id, quantity=1) -> int:
    """创建一笔 PENDING 订单，返回 order_id。"""
    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": quantity}]},
        headers=_auth(user_token),
    )
    assert resp.status_code == 200
    return resp.json()["data"]["id"]


# ================ 1. 用户支付自己的 PENDING 订单成功 ================
def test_pay_own_pending_order_success(client, user_token, admin_token):
    """用户支付自己的 PENDING 订单 → 200，status 变为 paid。"""
    sku_id = _create_sku(client, admin_token, "PayItem", "PAY-001", "50.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    response = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["status"] == "paid"


# ================ 2. 支付后状态变为 PAID（通过 GET 验证持久化）================
def test_status_becomes_paid_after_payment(client, user_token, admin_token):
    """支付后通过 GET 确认订单状态持久化为 paid。"""
    sku_id = _create_sku(client, admin_token, "PayItem2", "PAY-002", "30.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    # 支付
    pay_resp = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert pay_resp.status_code == 200

    # 通过 GET 验证持久化
    response = client.get(f"/orders/{order_id}", headers=_auth(user_token))
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "paid"


# ================ 3. 支付不存在的订单返回 404 ================
def test_pay_nonexistent_order_404(client, user_token):
    """支付不存在的订单 → OrderNotFoundError (404)。"""
    response = client.post("/orders/99999/pay", headers=_auth(user_token))
    assert response.status_code == 404
    assert response.json()["code"] == 10104  # OrderNotFoundError


# ================ 4. 用户不能支付其他人的订单 ================
def test_cannot_pay_others_order(client, user_token, admin_token, db_session):
    """用户 B 支付用户 A 的订单 → 404（不泄露订单存在性）。"""
    sku_id = _create_sku(client, admin_token, "PayItem3", "PAY-003", "20.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    # 创建用户 B
    user_b = create_test_user(db_session, name="UserB", email="user_b_pay@example.com")
    token_b = create_access_token(user_b.id)

    # 用户 B 尝试支付用户 A 的订单 → 404
    response = client.post(f"/orders/{order_id}/pay", headers=_auth(token_b))
    assert response.status_code == 404
    assert response.json()["code"] == 10104


# ================ 5. 重复支付失败 ================
def test_repeat_payment_fails(client, user_token, admin_token):
    """已支付的订单再次支付 → PAID → PAID 非法，返回 409。"""
    sku_id = _create_sku(client, admin_token, "PayItem4", "PAY-004", "15.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    # 第一次支付成功
    first = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert first.status_code == 200
    assert first.json()["data"]["status"] == "paid"

    # 第二次支付失败（PAID → PAID 不在状态机白名单中）
    second = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert second.status_code == 409
    assert second.json()["code"] == 10105  # OrderStatusError
