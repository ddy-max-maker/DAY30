"""支付回调测试：模拟支付平台回调（PENDING → PAID）。

业务规则变化（第二阶段）：支付入口从用户接口 POST /orders/{id}/pay
改为支付平台回调 POST /payments/callback（无 JWT，真实场景用验签）。
原"用户不能支付他人订单"用例随之失效——回调方是支付平台，
不存在用户身份越权问题，改写为"已取消订单不能支付"。

覆盖：
1. 首次回调支付成功：PENDING → PAID
2. 支付后状态持久化（GET 验证）
3. 回调不存在的订单返回 404
4. 已取消的订单不能支付（409）
5. 重复回调（同流水号）幂等返回成功
6. 已 PAID 但流水号不同 → 409 PaymentConflictError
7. 支付成功后 paid_at 被记录
"""



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


def _callback(client, order_id, reference="PAY-001"):
    """模拟支付平台回调。"""
    return client.post(
        "/payments/callback",
        json={
            "order_id": order_id,
            "payment_reference": reference,
            "status": "success",
        },
    )


# ================ 1. 首次回调支付成功 ================
def test_callback_pays_pending_order_success(client, user_token, admin_token):
    """首次回调：PENDING → PAID，接口 200。"""
    sku_id = _create_sku(client, admin_token, "PayItem", "PAY-001", "50.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    response = _callback(client, order_id, "PAY-20260924-0001")
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["status"] == "paid"


# ================ 2. 支付后状态持久化（GET 验证）================
def test_status_becomes_paid_after_callback(client, user_token, admin_token):
    """回调后通过 GET 确认订单状态持久化为 paid。"""
    sku_id = _create_sku(client, admin_token, "PayItem2", "PAY-002", "30.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    assert _callback(client, order_id, "PAY-20260924-0002").status_code == 200

    response = client.get(f"/orders/{order_id}", headers=_auth(user_token))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "paid"
    assert data["paid_at"] is not None  # 支付时间被记录


# ================ 3. 回调不存在的订单返回 404 ================
def test_callback_nonexistent_order_404(client):
    """回调不存在的订单 → OrderNotFoundError (404)。"""
    response = _callback(client, 99999, "PAY-20260924-0003")
    assert response.status_code == 404
    assert response.json()["code"] == 10104  # OrderNotFoundError


# ================ 4. 已取消的订单不能支付 ================
def test_callback_cancelled_order_409(client, user_token, admin_token):
    """已取消订单收到回调 → 409（状态机拒绝 CANCELLED → PAID）。"""
    sku_id = _create_sku(client, admin_token, "PayItem3", "PAY-003", "20.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    # 先取消
    cancel_resp = client.post(f"/orders/{order_id}/cancel", headers=_auth(user_token))
    assert cancel_resp.status_code == 200

    response = _callback(client, order_id, "PAY-20260924-0004")
    assert response.status_code == 409
    assert response.json()["code"] == 10105  # OrderStatusError


# ================ 5. 重复回调幂等 ================
def test_duplicate_callback_is_idempotent(client, user_token, admin_token):
    """同一流水号重复回调 → 200 幂等成功，订单保持 PAID 不重复修改。"""
    sku_id = _create_sku(client, admin_token, "PayItem4", "PAY-004", "15.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    first = _callback(client, order_id, "PAY-20260924-0005")
    assert first.status_code == 200
    assert first.json()["data"]["status"] == "paid"

    second = _callback(client, order_id, "PAY-20260924-0005")
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "paid"


# ================ 6. 已 PAID 但流水号不同 → 409 ================
def test_callback_with_different_reference_conflicts(client, user_token, admin_token):
    """订单已支付，收到不同流水号的回调 → 409 PaymentConflictError。"""
    sku_id = _create_sku(client, admin_token, "PayItem5", "PAY-005", "15.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    assert _callback(client, order_id, "PAY-20260924-0006A").status_code == 200

    response = _callback(client, order_id, "PAY-20260924-0006B")
    assert response.status_code == 409
    assert response.json()["code"] == 10109  # PaymentConflictError


# ================ 7. 支付失败通知不改变订单状态 ================
def test_failed_status_callback_does_not_change_order(client, user_token, admin_token):
    """status=failed 的回调仅确认接收，订单保持 PENDING。"""
    sku_id = _create_sku(client, admin_token, "PayItem6", "PAY-006", "15.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    response = client.post(
        "/payments/callback",
        json={
            "order_id": order_id,
            "payment_reference": "PAY-20260924-0007",
            "status": "failed",
        },
    )
    assert response.status_code == 200

    detail = client.get(f"/orders/{order_id}", headers=_auth(user_token))
    assert detail.json()["data"]["status"] == "pending"


# ================ 8. 用户不能直接把订单改成 PAID ================
def test_customer_cannot_pay_directly(client, user_token, admin_token):
    """业务规则变化：CUSTOMER 不再有直接支付接口（/orders/{id}/pay 已移除 → 404），
    支付状态切换只能由支付回调触发。
    """
    sku_id = _create_sku(client, admin_token, "PayItem7", "PAY-007", "15.00", 10)
    order_id = _create_order(client, user_token, sku_id)

    response = client.post(f"/orders/{order_id}/pay", headers=_auth(user_token))
    assert response.status_code == 404
