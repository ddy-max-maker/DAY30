"""关键业务流程测试。

使用 factory + db_session 直接写库准备测试数据，
再通过 API 验证业务行为，确保测试隔离、不依赖已有 id。

覆盖：
1. 普通用户不能创建商品（403）
2. 管理员创建商品成功（200）
3. 库存扣减：100 - 10 = 90
4. 库存不足：5 买 10 失败，库存仍 5
5. 订单取消：PENDING → CANCELLED
6. 重复取消失败
7. 用户不能查看其他用户订单（404）
"""

from sqlalchemy import select

from app.core.security import create_access_token
from app.models.inventory import Inventory
from tests.factories.user_factory import create_test_user


def _auth(token: str) -> dict:
    """把 token 字符串包装成 Authorization header。"""
    return {"Authorization": f"Bearer {token}"}


def _get_inventory(db_session, sku_id: int) -> Inventory | None:
    """查询某 SKU 的库存。

    先 rollback 结束当前事务，确保能看到 API 请求已 commit 的最新数据
    （MySQL REPEATABLE READ 下，旧事务看不到其他 session 的 commit）。
    """
    db_session.rollback()
    return db_session.scalar(select(Inventory).where(Inventory.sku_id == sku_id))


def _create_sku(
    client,
    admin_token,
    name="P",
    sku_code="SKU-1",
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


# ================ 1. RBAC：普通用户不能创建商品 ================


def test_normal_user_cannot_create_product(client, user_token):
    """普通用户访问 admin 创建商品接口 → 403。"""
    headers = _auth(user_token)

    response = client.post(
        "/admin/products",
        json={"name": "Hack", "description": "should fail"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == 10001


# ================ 2. RBAC：管理员创建商品成功 ================


def test_admin_can_create_product(client, admin_token):
    """管理员创建商品 → 200。"""
    headers = _auth(admin_token)

    response = client.post(
        "/admin/products",
        json={"name": "AdminProduct", "description": "created by admin"},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "AdminProduct"
    assert data["status"] == "on_sale"


# ================ 3. 库存扣减 ================


def test_stock_deducted_after_order(client, user_token, admin_token, db_session):
    """库存 100，购买 10，结果库存 90。"""
    sku_id = _create_sku(client, admin_token, stock=100)

    response = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 10}]},
        headers=_auth(user_token),
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0

    # 验证库存：从测试库直接查
    inv = _get_inventory(db_session, sku_id)
    assert inv is not None
    assert inv.stock == 90


# ================ 4. 库存不足 ================


def test_insufficient_stock_order_fails(client, user_token, admin_token, db_session):
    """库存 5，购买 10，失败，库存仍 5。"""
    sku_id = _create_sku(client, admin_token, stock=5)

    response = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 10}]},
        headers=_auth(user_token),
    )
    # InsufficientStockError → HTTP 409
    assert response.status_code == 409
    assert response.json()["code"] == 10103

    # 库存应该没变
    inv = _get_inventory(db_session, sku_id)
    assert inv is not None
    assert inv.stock == 5


# ================ 5. 订单取消：PENDING → CANCELLED ================


def test_cancel_order_pending_to_cancelled(client, user_token, admin_token, db_session):
    """PENDING 订单取消后状态变为 CANCELLED，库存恢复。"""
    sku_id = _create_sku(client, admin_token, stock=100)

    # 下单
    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 3}]},
        headers=_auth(user_token),
    )
    assert order_resp.status_code == 200
    order_id = order_resp.json()["data"]["id"]

    # 取消
    cancel_resp = client.post(
        f"/orders/{order_id}/cancel",
        headers=_auth(user_token),
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["data"]["status"] == "cancelled"

    # 库存恢复：100 - 3 + 3 = 100
    inv = _get_inventory(db_session, sku_id)
    assert inv.stock == 100


# ================ 6. 重复取消失败 ================


def test_double_cancel_fails(client, user_token, admin_token):
    """已取消的订单再次取消 → 幂等返回原订单（200）。

    业务规则变化（第二阶段）：取消接口幂等化——拿到订单行锁后发现
    已是 CANCELLED 时直接返回当前订单，不再抛 409；
    重复取消不会重复恢复库存。
    """
    sku_id = _create_sku(client, admin_token, stock=100)

    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=_auth(user_token),
    )
    order_id = order_resp.json()["data"]["id"]

    # 第一次取消成功
    first = client.post(f"/orders/{order_id}/cancel", headers=_auth(user_token))
    assert first.status_code == 200

    # 第二次取消：幂等返回原订单
    second = client.post(f"/orders/{order_id}/cancel", headers=_auth(user_token))
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "cancelled"


# ================ 7. 用户不能查看其他用户订单 ================


def test_user_cannot_view_others_order(client, user_token, admin_token, db_session):
    """用户 A 的订单，用户 B 查看 → 404（不泄露订单存在性）。"""
    # 创建用户 A 并下单
    sku_id = _create_sku(client, admin_token, stock=100)
    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=_auth(user_token),
    )
    order_id = order_resp.json()["data"]["id"]

    # 创建用户 B（用 factory 直接写库 + 生成 token）
    user_b = create_test_user(db_session, name="UserB", email="user_b@example.com")
    token_b = create_access_token(user_b.id)

    # 用户 B 查看用户 A 的订单 → 404
    response = client.get(
        f"/orders/{order_id}",
        headers=_auth(token_b),
    )
    assert response.status_code == 404
    assert response.json()["code"] == 10104
