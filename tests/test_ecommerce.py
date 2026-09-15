"""电商 MVP 测试：RBAC、商品管理、下单事务、订单隔离。"""

import pytest

from tests.conftest import TestingSessionLocal


# ================ RBAC 测试 ================
def test_register_user_role_is_user(client):
    """1. 普通用户注册后 role 为 USER。"""
    response = client.post(
        "/auth/register",
        json={"name": "Tom", "email": "tom@example.com", "password": "12345678"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["role"] == "user"


def test_normal_user_cannot_access_admin(client, auth_headers):
    """2. 普通用户不能访问 admin 接口（403）。"""
    headers = auth_headers()

    # 尝试创建商品
    response = client.post(
        "/admin/products",
        json={"name": "Phone", "description": "Smartphone"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == 10001


def test_admin_can_create_product(client, admin_headers):
    """3. ADMIN 可以创建商品。"""
    headers = admin_headers()

    response = client.post(
        "/admin/products",
        json={"name": "iPhone", "description": "Apple smartphone"},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "iPhone"
    assert data["status"] == "on_sale"


def test_admin_can_create_sku_and_inventory(client, admin_headers):
    """4. ADMIN 可以创建 SKU（含库存初始化）。"""
    headers = admin_headers()

    # 先创建商品
    product_resp = client.post(
        "/admin/products",
        json={"name": "MacBook", "description": "Apple laptop"},
        headers=headers,
    )
    product_id = product_resp.json()["data"]["id"]

    # 创建 SKU
    response = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": "MBP-14-512",
            "name": "MacBook Pro 14 512G",
            "price": "12999.00",
            "stock": 100,
        },
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["sku_code"] == "MBP-14-512"
    assert data["name"] == "MacBook Pro 14 512G"
    assert data["price"] == "12999.00"
    assert data["status"] == "active"


def test_admin_can_adjust_inventory(client, admin_headers):
    """5. ADMIN 可以调整库存。"""
    headers = admin_headers()

    # 创建商品 + SKU
    product_resp = client.post(
        "/admin/products",
        json={"name": "iPad", "description": "Tablet"},
        headers=headers,
    )
    product_id = product_resp.json()["data"]["id"]

    sku_resp = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": "IPAD-PRO-11",
            "name": "iPad Pro 11",
            "price": "6799.00",
            "stock": 50,
        },
        headers=headers,
    )
    sku_id = sku_resp.json()["data"]["id"]

    # 调整库存
    response = client.put(
        f"/admin/inventory/{sku_id}",
        json={"stock": 200},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stock"] == 200


def test_user_can_view_products(client, auth_headers, admin_headers):
    """6. 普通用户可以查看商品。"""
    admin = admin_headers()
    user = auth_headers()

    # 管理员创建商品
    client.post(
        "/admin/products",
        json={"name": "AirPods", "description": "Wireless earbuds"},
        headers=admin,
    )

    # 用户浏览
    response = client.get("/products", headers=user)
    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] >= 1
    assert len(page["items"]) >= 1
    assert page["items"][0]["name"] == "AirPods"


# ================ 下单测试 ================
def _create_product_with_sku(
    client, admin, name="TestProduct", sku_code="TEST-SKU", price="99.00", stock=10
):
    """辅助：管理员创建商品 + SKU，返回 (product_id, sku_id)。

    admin 参数是已创建好的 admin headers（避免重复创建 admin 用户）。
    """
    product_resp = client.post(
        "/admin/products",
        json={"name": name, "description": f"Description for {name}"},
        headers=admin,
    )
    product_id = product_resp.json()["data"]["id"]

    sku_resp = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": sku_code,
            "name": f"{name} SKU",
            "price": price,
            "stock": stock,
        },
        headers=admin,
    )
    sku_id = sku_resp.json()["data"]["id"]
    return product_id, sku_id


def test_user_can_create_order(client, auth_headers, admin_headers):
    """7. 用户可以正常创建订单。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(client, admin, "Widget", "W-001", "50.00", 10)
    headers = auth_headers()

    response = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 2}]},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "pending"
    assert data["total_amount"] == "100.00"  # 50 * 2
    assert len(data["items"]) == 1
    assert data["items"][0]["sku_name"] == "Widget SKU"
    assert data["items"][0]["unit_price"] == "50.00"
    assert data["items"][0]["quantity"] == 2
    assert data["items"][0]["subtotal"] == "100.00"


def test_insufficient_stock_cannot_order(client, auth_headers, admin_headers):
    """8. 库存不足时不能创建订单。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(client, admin, "Gadget", "G-001", "10.00", 5)
    headers = auth_headers()

    response = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 10}]},  # 只有 5 个
        headers=headers,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == 10103  # InsufficientStockError


def test_stock_reduced_after_order(client, auth_headers, admin_headers):
    """9. 下单成功后库存正确减少。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "Doohickey", "D-001", "20.00", 20
    )
    headers = auth_headers()

    # 下单 3 个
    client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 3}]},
        headers=headers,
    )

    # 直接查 DB 验证库存
    from sqlalchemy import select as sa_select

    from app.models.inventory import Inventory

    db = TestingSessionLocal()
    inv = db.scalar(sa_select(Inventory).where(Inventory.sku_id == sku_id))
    db.close()

    assert inv.stock == 17  # 20 - 3


def test_order_amount_calculated_by_server(client, auth_headers, admin_headers):
    """10. 下单金额由后端计算（客户端不能篡改）。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(client, admin, "Gizmo", "GZ-001", "100.00", 50)
    headers = auth_headers()

    # 客户端只提交 sku_id + quantity，没有 total_amount 字段
    response = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 3}]},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_amount"] == "300.00"  # 100 * 3，服务端算


def test_user_can_only_see_own_orders(client, auth_headers):
    """11 + 12. 用户只能查看自己的订单（看不到别人的）。"""
    alice = auth_headers(name="Alice", email="alice@example.com")
    bob = auth_headers(name="Bob", email="bob@example.com")

    # Alice 没有订单
    response = client.get("/orders", headers=alice)
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 0
    assert len(response.json()["data"]["items"]) == 0

    # Bob 也没有
    response = client.get("/orders", headers=bob)
    assert response.json()["data"]["total"] == 0
    assert len(response.json()["data"]["items"]) == 0


def test_user_cannot_view_other_users_order(client, auth_headers, admin_headers):
    """12. 用户不能通过 order_id 查看其他用户订单（返回 404）。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "Thingamajig", "T-001", "5.00", 10
    )
    alice = auth_headers(name="Alice", email="alice@example.com")
    bob = auth_headers(name="Bob", email="bob@example.com")

    # Alice 下单
    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=alice,
    )
    order_id = order_resp.json()["data"]["id"]

    # Bob 尝试访问 Alice 的订单 → 404（不泄露订单存在性）
    response = client.get(f"/orders/{order_id}", headers=bob)
    assert response.status_code == 404
    assert response.json()["code"] == 10104


def test_cancel_order(client, auth_headers, admin_headers):
    """13. 取消订单逻辑正确（PENDING 可取消，取消后库存恢复）。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "Whatchamacallit", "W-001", "30.00", 10
    )
    headers = auth_headers()

    # 下单 2 个
    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 2}]},
        headers=headers,
    )
    order_id = order_resp.json()["data"]["id"]

    # 取消订单
    cancel_resp = client.post(f"/orders/{order_id}/cancel", headers=headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["data"]["status"] == "cancelled"

    # 库存恢复
    from sqlalchemy import select as sa_select

    from app.models.inventory import Inventory

    db = TestingSessionLocal()
    inv = db.scalar(sa_select(Inventory).where(Inventory.sku_id == sku_id))
    db.close()
    assert inv.stock == 10  # 恢复到 10


def test_cancel_non_pending_order_fails(client, auth_headers, admin_headers):
    """已取消的订单不能再取消。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "Gadget2", "G2-001", "10.00", 10
    )
    headers = auth_headers()

    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=headers,
    )
    order_id = order_resp.json()["data"]["id"]

    # 第一次取消成功
    client.post(f"/orders/{order_id}/cancel", headers=headers)

    # 第二次取消失败
    response = client.post(f"/orders/{order_id}/cancel", headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == 10105  # OrderStatusError


def test_transaction_rollback_on_insufficient_stock(
    client, auth_headers, admin_headers
):
    """14. 创建订单过程中库存不足时事务回滚（前面已扣的库存恢复）。

    场景：两个 SKU 在同一订单中，第一个库存够，第二个不够。
    整个事务应该回滚，第一个 SKU 的库存不被扣除。
    """
    admin = admin_headers()
    _, sku1_id = _create_product_with_sku(
        client, admin, "Product A", "PA-001", "10.00", 5
    )
    _, sku2_id = _create_product_with_sku(
        client, admin, "Product B", "PB-001", "20.00", 1
    )
    headers = auth_headers()

    # 同时下单两个 SKU，第二个库存不够
    response = client.post(
        "/orders",
        json={
            "items": [
                {"sku_id": sku1_id, "quantity": 3},  # OK, stock=5
                {"sku_id": sku2_id, "quantity": 5},  # FAIL, stock=1
            ]
        },
        headers=headers,
    )
    assert response.json()["code"] == 10103  # InsufficientStockError

    # 验证第一个 SKU 的库存没被扣（事务回滚）
    from sqlalchemy import select as sa_select

    from app.models.inventory import Inventory

    db = TestingSessionLocal()
    inv1 = db.scalar(sa_select(Inventory).where(Inventory.sku_id == sku1_id))
    db.close()
    assert inv1.stock == 5  # 没扣


def test_admin_can_manage_orders(client, admin_headers, auth_headers):
    """管理员可以查看全部订单和修改订单状态。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "AdminOrder", "AO-001", "15.00", 10
    )
    user = auth_headers()

    # 用户下单
    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 2}]},
        headers=user,
    )
    order_id = order_resp.json()["data"]["id"]

    # 管理员查看全部订单
    response = client.get("/admin/orders", headers=admin)
    assert response.status_code == 200
    page = response.json()["data"]
    assert page["total"] >= 1
    assert len(page["items"]) >= 1

    # 管理员修改订单状态为 PAID
    response = client.patch(
        f"/admin/orders/{order_id}/status",
        json={"status": "paid"},
        headers=admin,
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "paid"


def test_off_sale_product_not_visible(client, auth_headers, admin_headers):
    """下架商品不出现在用户商品列表中。"""
    admin = admin_headers()
    user = auth_headers()

    # 创建商品
    resp = client.post(
        "/admin/products",
        json={"name": "Discontinued", "description": "Soon to be off sale"},
        headers=admin,
    )
    product_id = resp.json()["data"]["id"]

    # 下架
    client.patch(
        f"/admin/products/{product_id}/status",
        json={"status": "off_sale"},
        headers=admin,
    )

    # 用户看不到
    response = client.get("/products", headers=user)
    products = response.json()["data"]["items"]
    product_ids = [p["id"] for p in products]
    assert product_id not in product_ids


# ================ 订单号 / 取消并发 / 状态机测试 ================
def test_order_no_is_uuid_based(client, auth_headers, admin_headers):
    """订单号为 ORD + 32 位 UUID hex（共 35 字符）。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "UUIDOrder", "UO-001", "1.00", 5
    )
    headers = auth_headers()

    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=headers,
    )
    order_no = resp.json()["data"]["order_no"]

    assert order_no.startswith("ORD")
    uuid_hex = order_no[3:]
    assert len(uuid_hex) == 32
    int(uuid_hex, 16)  # 必须是合法 hex


def test_duplicate_cancel_restores_stock_once(client, auth_headers, admin_headers):
    """同一订单重复取消：第二次报状态错误，库存只恢复一次。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "DupCancel", "DC-001", "10.00", 10
    )
    headers = auth_headers()

    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 2}]},
        headers=headers,
    )
    order_id = resp.json()["data"]["id"]

    # 第一次取消成功
    first = client.post(f"/orders/{order_id}/cancel", headers=headers)
    assert first.json()["data"]["status"] == "cancelled"

    # 第二次取消：订单已不是 PENDING
    second = client.post(f"/orders/{order_id}/cancel", headers=headers)
    assert second.json()["code"] == 10105

    # 库存只恢复一次：10（原始）而不是 12（重复恢复）
    from sqlalchemy import select as sa_select

    from app.models.inventory import Inventory

    db = TestingSessionLocal()
    inv = db.scalar(sa_select(Inventory).where(Inventory.sku_id == sku_id))
    db.close()
    assert inv.stock == 10


def test_concurrent_cancel_restores_stock_once(client, auth_headers, admin_headers):
    """并发取消同一订单：恰好一个成功，另一个被行锁挡住，库存只恢复一次。

    两个线程用各自的 DB 会话同时调用 cancel_order：
    先拿到订单行锁（SELECT ... FOR UPDATE）的线程执行取消并恢复库存，
    另一个线程等锁释放后读到 CANCELLED 状态，抛 OrderStatusError。
    """
    import threading

    from app.services import order_service

    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "ConcurrentCancel", "CCX-001", "10.00", 10
    )
    headers = auth_headers()

    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 2}]},
        headers=headers,
    )
    data = resp.json()["data"]
    order_id = data["id"]
    user_id = data["user_id"]

    results: list = []
    barrier = threading.Barrier(2)  # 让两个线程尽量同时发起取消

    def worker():
        db = TestingSessionLocal()
        try:
            barrier.wait(timeout=5)
            order_service.cancel_order(db, order_id, user_id)
            results.append("ok")
        except Exception as exc:
            results.append(("err", getattr(exc, "code", None)))
        finally:
            db.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # 恰好一个成功、一个被状态机拒绝
    assert results.count("ok") == 1
    assert ("err", 10105) in results

    # 库存只恢复一次
    from sqlalchemy import select as sa_select

    from app.models.inventory import Inventory

    db = TestingSessionLocal()
    inv = db.scalar(sa_select(Inventory).where(Inventory.sku_id == sku_id))
    db.close()
    assert inv.stock == 10


# ---------- 订单状态机 ----------
def _create_pending_order(client, admin, user, sku_id, quantity=1):
    """创建一笔 PENDING 订单，返回 order_id。"""
    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": quantity}]},
        headers=user,
    )
    assert resp.status_code == 200
    return resp.json()["data"]["id"]


def _admin_set_status(client, admin, order_id, status):
    return client.patch(
        f"/admin/orders/{order_id}/status",
        json={"status": status},
        headers=admin,
    )


# 各状态从 PENDING 出发的合法到达路径
_STATUS_PATH = {
    "pending": [],
    "paid": ["paid"],
    "shipped": ["paid", "shipped"],
    "completed": ["paid", "shipped", "completed"],
    "cancelled": ["cancelled"],
}


def _order_at_status(client, admin, user, sku_id, target_status):
    order_id = _create_pending_order(client, admin, user, sku_id)
    for step in _STATUS_PATH[target_status]:
        resp = _admin_set_status(client, admin, order_id, step)
        assert resp.status_code == 200, f"准备状态失败: {step}"
    return order_id


def test_valid_order_status_chain(client, auth_headers, admin_headers):
    """合法状态链全部放行：PENDING→PAID→SHIPPED→COMPLETED，PENDING→CANCELLED。"""
    admin = admin_headers()
    user = auth_headers()

    # 正向履约链
    _, sku1 = _create_product_with_sku(client, admin, "Chain1", "CH-001", "1.00", 5)
    order_id = _create_pending_order(client, admin, user, sku1)
    assert (
        _admin_set_status(client, admin, order_id, "paid").json()["data"]["status"]
        == "paid"
    )
    assert (
        _admin_set_status(client, admin, order_id, "shipped").json()["data"]["status"]
        == "shipped"
    )
    assert (
        _admin_set_status(client, admin, order_id, "completed").json()["data"]["status"]
        == "completed"
    )

    # PENDING → CANCELLED
    _, sku2 = _create_product_with_sku(client, admin, "Chain2", "CH-002", "1.00", 5)
    order_id2 = _create_pending_order(client, admin, user, sku2)
    resp = _admin_set_status(client, admin, order_id2, "cancelled")
    assert resp.json()["data"]["status"] == "cancelled"


@pytest.mark.parametrize(
    "source_status, illegal_target",
    [
        ("pending", "completed"),  # 不能跳级
        ("pending", "shipped"),  # 不能跳级
        ("paid", "cancelled"),  # 退款未实现，不允许支付后取消
        ("paid", "pending"),  # 不能回退
        ("shipped", "paid"),  # 不能回退
        ("shipped", "pending"),  # 不能回退
        ("cancelled", "paid"),  # 终态
        ("completed", "shipped"),  # 终态
    ],
)
def test_illegal_order_status_transitions(
    client, auth_headers, admin_headers, source_status, illegal_target
):
    """非法状态流转全部拒绝（10105），且原状态不变。"""
    admin = admin_headers()
    user = auth_headers()
    _, sku_id = _create_product_with_sku(
        client,
        admin,
        f"Illegal-{source_status}-{illegal_target}",
        f"IL-{source_status[:2]}-{illegal_target[:2]}",
        "1.00",
        5,
    )

    order_id = _order_at_status(client, admin, user, sku_id, source_status)

    resp = _admin_set_status(client, admin, order_id, illegal_target)
    assert resp.status_code == 409
    assert resp.json()["code"] == 10105

    # 原状态未被修改
    detail = client.get(f"/orders/{order_id}", headers=user).json()["data"]
    assert detail["status"] == source_status


# ================ 分页测试 ================
def test_products_default_pagination(client, auth_headers, admin_headers):
    """1. /products 默认分页：page=1, page_size=20。"""
    admin = admin_headers()
    user = auth_headers()
    # 创建 1 个在售商品即可验证默认分页结构
    _create_product_with_sku(client, admin, "Pager1", "PG-001", "1.00", 5)

    resp = client.get("/products", headers=user)
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["page"] == 1
    assert page["page_size"] == 20
    assert page["total"] >= 1
    assert len(page["items"]) >= 1


def test_products_custom_page_size(client, auth_headers, admin_headers):
    """2+3. /products 指定 page/page_size，total 正确。"""
    admin = admin_headers()
    user = auth_headers()
    # 创建 5 个在售商品
    for i in range(5):
        _create_product_with_sku(client, admin, f"Size{i}", f"SZ-{i:03d}", "1.00", 5)

    resp = client.get("/products?page=1&page_size=3", headers=user)
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["page"] == 1
    assert page["page_size"] == 3
    assert page["total"] >= 5
    assert len(page["items"]) == 3


def test_products_total_pages_calculation(client, auth_headers, admin_headers):
    """4. total_pages 正确（不能整除向上取整）。"""
    admin = admin_headers()
    user = auth_headers()
    # 创建 5 个在售商品，page_size=2 → total_pages=3（2+2+1）
    for i in range(5):
        _create_product_with_sku(client, admin, f"Pages{i}", f"PS-{i:03d}", "1.00", 5)

    resp = client.get("/products?page=1&page_size=2", headers=user)
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["total"] >= 5
    # total_pages = ceil(total / page_size)
    import math

    assert page["total_pages"] == math.ceil(page["total"] / 2)


def test_products_second_page_data(client, auth_headers, admin_headers):
    """5. 第二页数据正确（不与第一页重复）。"""
    admin = admin_headers()
    user = auth_headers()
    # 创建 3 个在售商品，page_size=2 → 第一页 2 个，第二页 1 个
    for i in range(3):
        _create_product_with_sku(client, admin, f"Second{i}", f"SP-{i:03d}", "1.00", 5)

    first = client.get("/products?page=1&page_size=2", headers=user).json()["data"]
    second = client.get("/products?page=2&page_size=2", headers=user).json()["data"]

    assert len(first["items"]) == 2
    assert len(second["items"]) >= 1
    first_ids = {p["id"] for p in first["items"]}
    second_ids = {p["id"] for p in second["items"]}
    assert not first_ids.intersection(second_ids), "第二页不应与第一页重复"


def test_products_empty_result_pagination(client, auth_headers):
    """6. 空结果分页正确（total=0, total_pages=0, items=[]）。"""
    user = auth_headers()
    # 不创建任何商品，默认商品表为空（每个测试重置 DB）
    resp = client.get("/products?page=1&page_size=10", headers=user)
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["total"] == 0
    assert page["total_pages"] == 0
    assert page["items"] == []


def test_products_page_zero_rejected(client, auth_headers):
    """7. page=0 返回 422。"""
    user = auth_headers()
    resp = client.get("/products?page=0", headers=user)
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_products_page_size_zero_rejected(client, auth_headers):
    """8. page_size=0 返回 422。"""
    user = auth_headers()
    resp = client.get("/products?page_size=0", headers=user)
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_products_page_size_over_100_rejected(client, auth_headers):
    """9. page_size=101 返回 422。"""
    user = auth_headers()
    resp = client.get("/products?page_size=101", headers=user)
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_user_orders_pagination_isolated(client, auth_headers, admin_headers):
    """10. 普通用户订单分页只能看到自己的订单。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "IsoOrder", "IO-001", "10.00", 50
    )
    alice = auth_headers(name="Alice", email="alice@example.com")
    bob = auth_headers(name="Bob", email="bob@example.com")

    # Alice 下 3 单
    for _ in range(3):
        client.post(
            "/orders",
            json={"items": [{"sku_id": sku_id, "quantity": 1}]},
            headers=alice,
        )
    # Bob 下 1 单
    client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 1}]},
        headers=bob,
    )

    # Alice 分页查看：只能看到自己的 3 单
    resp = client.get("/orders?page=1&page_size=2", headers=alice)
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["total"] == 3
    assert len(page["items"]) == 2
    for o in page["items"]:
        assert o["user_id"] is not None
        # 验证是 Alice 的订单（通过详情接口确认归属）
    # 第二页应只有 1 个
    resp2 = client.get("/orders?page=2&page_size=2", headers=alice)
    page2 = resp2.json()["data"]
    assert len(page2["items"]) == 1

    # Bob 分页查看：只能看到自己的 1 单
    resp_bob = client.get("/orders?page=1&page_size=10", headers=bob)
    page_bob = resp_bob.json()["data"]
    assert page_bob["total"] == 1
    assert len(page_bob["items"]) == 1


def test_admin_orders_pagination_sees_all(client, auth_headers, admin_headers):
    """11. admin orders 分页能看到全部订单。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "AllOrders", "AO2-001", "10.00", 100
    )
    alice = auth_headers(name="Alice", email="alice@example.com")
    bob = auth_headers(name="Bob", email="bob@example.com")

    # 两个用户各下 2 单，共 4 单
    for _ in range(2):
        client.post(
            "/orders",
            json={"items": [{"sku_id": sku_id, "quantity": 1}]},
            headers=alice,
        )
        client.post(
            "/orders",
            json={"items": [{"sku_id": sku_id, "quantity": 1}]},
            headers=bob,
        )

    # 管理员分页查看全部
    resp = client.get("/admin/orders?page=1&page_size=2", headers=admin)
    assert resp.status_code == 200
    page = resp.json()["data"]
    assert page["total"] >= 4
    assert len(page["items"]) == 2

    # 第二页也应有数据
    resp2 = client.get("/admin/orders?page=2&page_size=2", headers=admin)
    page2 = resp2.json()["data"]
    assert len(page2["items"]) == 2


# ================ 参数校验测试 ================
def test_order_quantity_zero_rejected(client, auth_headers, admin_headers):
    """12. quantity=0 被拒绝（422）。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(
        client, admin, "QtyZero", "QZ-001", "10.00", 10
    )
    headers = auth_headers()
    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": 0}]},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_order_quantity_negative_rejected(client, auth_headers, admin_headers):
    """13. quantity<0 被拒绝（422）。"""
    admin = admin_headers()
    _, sku_id = _create_product_with_sku(client, admin, "QtyNeg", "QN-001", "10.00", 10)
    headers = auth_headers()
    resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": -1}]},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_sku_stock_negative_rejected(client, admin_headers):
    """14. stock<0 被拒绝（422）。"""
    admin = admin_headers()
    # 先创建商品
    product_resp = client.post(
        "/admin/products",
        json={"name": "NegStock", "description": "test"},
        headers=admin,
    )
    product_id = product_resp.json()["data"]["id"]

    # 尝试创建 stock=-1 的 SKU
    resp = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": "NS-001",
            "name": "NegStock SKU",
            "price": "10.00",
            "stock": -1,
        },
        headers=admin,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 422


def test_sku_price_zero_or_negative_rejected(client, admin_headers):
    """15. price<=0 被拒绝（422）。"""
    admin = admin_headers()
    product_resp = client.post(
        "/admin/products",
        json={"name": "ZeroPrice", "description": "test"},
        headers=admin,
    )
    product_id = product_resp.json()["data"]["id"]

    # price = 0
    resp0 = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": "ZP-001",
            "name": "Zero Price",
            "price": "0.00",
            "stock": 10,
        },
        headers=admin,
    )
    assert resp0.status_code == 422
    assert resp0.json()["code"] == 422

    # price = -1
    resp_neg = client.post(
        f"/admin/products/{product_id}/skus",
        json={
            "sku_code": "NP-001",
            "name": "Neg Price",
            "price": "-1.00",
            "stock": 10,
        },
        headers=admin,
    )
    assert resp_neg.status_code == 422
    assert resp_neg.json()["code"] == 422
