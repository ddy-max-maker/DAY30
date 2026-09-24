"""RBAC 三角色权限与资源归属测试。

覆盖：
- RBAC：CUSTOMER/MERCHANT/ADMIN 角色边界
- 商品资源归属：商家只能操作自己的商品/SKU/库存
- 订单资源归属：消费者/商家/管理员各自可见范围
- 跨商家订单拒绝（409）
- 安全字段：客户端无法伪造 merchant_id / customer_id / total_amount
"""

# ================ RBAC 角色边界 ================
def test_customer_cannot_create_product(client, auth_headers):
    """CUSTOMER 不能创建商品（商家接口 403）。"""
    headers = auth_headers()
    response = client.post(
        "/merchant/products",
        json={"name": "Phone", "description": "Smartphone"},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == 10001


def test_customer_cannot_create_product_via_admin(client, auth_headers):
    """CUSTOMER 不能走 admin 创建商品接口（403）。"""
    headers = auth_headers()
    response = client.post(
        "/admin/products",
        json={"name": "Phone", "description": "Smartphone"},
        headers=headers,
    )
    assert response.status_code == 403


def test_merchant_can_create_product(client, merchant_headers):
    """MERCHANT 可以创建商品，归属自动写入当前商家。"""
    headers = merchant_headers()
    response = client.post(
        "/merchant/products",
        json={"name": "Phone", "description": "Smartphone"},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "Phone"
    me = client.get("/users/me", headers=headers).json()["data"]
    assert data["merchant_id"] == me["id"]


def test_merchant_cannot_access_admin_api(client, merchant_headers):
    """MERCHANT 不能访问 admin 接口（403）。"""
    headers = merchant_headers()
    response = client.get("/admin/orders", headers=headers)
    assert response.status_code == 403


def test_admin_can_still_create_product(client, admin_headers):
    """ADMIN 保留创建商品能力（兼容逻辑，归属 admin 自身）。"""
    headers = admin_headers()
    response = client.post(
        "/admin/products",
        json={"name": "PlatformItem", "description": "Operated by admin"},
        headers=headers,
    )
    assert response.status_code == 200


def test_merchant_cannot_create_order(client, merchant_headers):
    """MERCHANT 不是下单业务角色（403）。"""
    headers = merchant_headers()
    response = client.post(
        "/orders",
        json={"items": [{"sku_id": 1, "quantity": 1}]},
        headers=headers,
    )
    assert response.status_code == 403


def test_admin_cannot_create_order(client, admin_headers):
    """ADMIN 不是下单业务角色（403）。"""
    headers = admin_headers()
    response = client.post(
        "/orders",
        json={"items": [{"sku_id": 1, "quantity": 1}]},
        headers=headers,
    )
    assert response.status_code == 403


def test_merchant_cannot_use_cart(client, merchant_headers):
    """MERCHANT 不能使用购物车（购物车仅 CUSTOMER）。"""
    headers = merchant_headers()
    response = client.post(
        "/cart/items",
        json={"sku_id": 1, "quantity": 1},
        headers=headers,
    )
    assert response.status_code == 403


# ================ 商品资源归属 ================
def _create_product_with_sku(client, headers, name, sku_code, price="10.00", stock=10):
    """辅助：通过商家接口创建商品 + SKU，返回 (product, sku) 数据。"""
    product_resp = client.post(
        "/merchant/products",
        json={"name": name, "description": f"Description for {name}"},
        headers=headers,
    )
    assert product_resp.status_code == 200
    product = product_resp.json()["data"]

    sku_resp = client.post(
        f"/merchant/products/{product['id']}/skus",
        json={
            "sku_code": sku_code,
            "name": f"{name} SKU",
            "price": price,
            "stock": stock,
        },
        headers=headers,
    )
    assert sku_resp.status_code == 200
    return product, sku_resp.json()["data"]


def test_merchant_can_update_own_product(client, merchant_headers):
    """Merchant A 修改自己的商品 → 成功。"""
    headers = merchant_headers()
    product, _ = _create_product_with_sku(client, headers, "OwnProduct", "OWN-001")

    response = client.put(
        f"/merchant/products/{product['id']}",
        json={"name": "Renamed"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Renamed"


def test_merchant_b_cannot_update_merchant_a_product(client, merchant_headers):
    """Merchant B 修改 Merchant A 的商品 → 403。"""
    headers_a = merchant_headers(name="MerchantA", email="merchant_a@example.com")
    product_a, _ = _create_product_with_sku(client, headers_a, "ProductA", "PA-001")

    headers_b = merchant_headers(name="MerchantB", email="merchant_b@example.com")
    response = client.put(
        f"/merchant/products/{product_a['id']}",
        json={"name": "Hijacked"},
        headers=headers_b,
    )
    assert response.status_code == 403
    assert response.json()["code"] == 10001


def test_customer_cannot_update_product(client, auth_headers, merchant_headers):
    """Customer 修改商家的商品 → 403（角色不满足）。"""
    merchant = merchant_headers()
    product, _ = _create_product_with_sku(client, merchant, "CustProd", "CP-001")

    response = client.put(
        f"/merchant/products/{product['id']}",
        json={"name": "Tampered"},
        headers=auth_headers(),
    )
    assert response.status_code == 403


def test_merchant_b_cannot_adjust_a_inventory(client, merchant_headers):
    """Merchant B 不能修改 Merchant A 的库存 → 403。"""
    headers_a = merchant_headers(name="MerchantA2", email="merchant_a2@example.com")
    _, sku_a = _create_product_with_sku(client, headers_a, "StockProd", "SP-001")

    headers_b = merchant_headers(name="MerchantB2", email="merchant_b2@example.com")
    response = client.put(
        f"/merchant/inventory/{sku_a['id']}",
        json={"stock": 0},
        headers=headers_b,
    )
    assert response.status_code == 403


def test_merchant_cannot_see_others_products(client, merchant_headers):
    """商家商品列表只包含自己的商品。"""
    headers_a = merchant_headers(name="MerchantA3", email="merchant_a3@example.com")
    product_a, _ = _create_product_with_sku(client, headers_a, "ListProdA", "LP-A1")

    headers_b = merchant_headers(name="MerchantB3", email="merchant_b3@example.com")
    response = client.get("/merchant/products", headers=headers_b)
    assert response.status_code == 200
    product_ids = [p["id"] for p in response.json()["data"]]
    assert product_a["id"] not in product_ids


# ================ 订单资源归属 ================
def test_customer_order_flow_and_isolation(client, auth_headers, merchant_headers):
    """Customer A 下单成功并能查看自己的订单；Customer B 不能查看（404）。"""
    merchant = merchant_headers()
    _, sku = _create_product_with_sku(client, merchant, "IsoProd", "ISO-001")

    alice = auth_headers(name="Alice", email="alice@example.com")
    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku["id"], "quantity": 1}]},
        headers=alice,
    )
    assert order_resp.status_code == 200
    order = order_resp.json()["data"]
    assert order["merchant_id"] > 0

    bob = auth_headers(name="Bob", email="bob@example.com")
    response = client.get(f"/orders/{order['id']}", headers=bob)
    assert response.status_code == 404


def test_merchant_sees_only_own_orders(client, auth_headers, merchant_headers):
    """Merchant A 能查看包含自己商品的订单；Merchant B 不能（404）。"""
    headers_a = merchant_headers(name="MerchantA4", email="merchant_a4@example.com")
    _, sku_a = _create_product_with_sku(client, headers_a, "MOrderProd", "MO-001")

    customer = auth_headers(name="Carol", email="carol@example.com")
    order_resp = client.post(
        "/orders",
        json={"items": [{"sku_id": sku_a["id"], "quantity": 1}]},
        headers=customer,
    )
    order_id = order_resp.json()["data"]["id"]

    response = client.get(f"/merchant/orders/{order_id}", headers=headers_a)
    assert response.status_code == 200

    headers_b = merchant_headers(name="MerchantB4", email="merchant_b4@example.com")
    response = client.get(f"/merchant/orders/{order_id}", headers=headers_b)
    assert response.status_code == 404

    response = client.get("/merchant/orders", headers=headers_b)
    assert response.status_code == 200
    assert len(response.json()["data"]) == 0


def test_admin_can_view_all_orders(
    client, auth_headers, merchant_headers, admin_headers
):
    """Admin 能查看全部订单（含各商家订单）。"""
    merchant = merchant_headers(name="MerchantA5", email="merchant_a5@example.com")
    _, sku = _create_product_with_sku(client, merchant, "AdminView", "AV-001")

    customer = auth_headers(name="Dave", email="dave@example.com")
    client.post(
        "/orders",
        json={"items": [{"sku_id": sku["id"], "quantity": 1}]},
        headers=customer,
    )

    response = client.get("/admin/orders", headers=admin_headers())
    assert response.status_code == 200
    assert len(response.json()["data"]) >= 1


# ================ 跨商家订单 ================
def test_cross_merchant_order_rejected(client, auth_headers, merchant_headers):
    """同一订单购买两个商家的商品 → 409 拒绝。"""
    headers_a = merchant_headers(name="MerchantA6", email="merchant_a6@example.com")
    _, sku_a = _create_product_with_sku(client, headers_a, "CrossA", "CR-A1")

    headers_b = merchant_headers(name="MerchantB6", email="merchant_b6@example.com")
    _, sku_b = _create_product_with_sku(client, headers_b, "CrossB", "CR-B1")

    customer = auth_headers(name="Eve", email="eve@example.com")
    response = client.post(
        "/orders",
        json={
            "items": [
                {"sku_id": sku_a["id"], "quantity": 1},
                {"sku_id": sku_b["id"], "quantity": 1},
            ]
        },
        headers=customer,
    )
    assert response.status_code == 409
    assert response.json()["code"] == 10107


def test_same_merchant_multi_sku_order_ok(client, auth_headers, merchant_headers):
    """同一商家的多个 SKU 同单购买 → 正常创建。"""
    headers = merchant_headers()
    _, sku1 = _create_product_with_sku(client, headers, "SameA", "SM-A1")
    _, sku2 = _create_product_with_sku(client, headers, "SameB", "SM-B1")

    customer = auth_headers(name="Grace", email="grace@example.com")
    response = client.post(
        "/orders",
        json={
            "items": [
                {"sku_id": sku1["id"], "quantity": 1},
                {"sku_id": sku2["id"], "quantity": 2},
            ]
        },
        headers=customer,
    )
    assert response.status_code == 200
    assert len(response.json()["data"]["items"]) == 2


# ================ 安全字段 ================
def test_merchant_id_cannot_be_forged(client, merchant_headers):
    """客户端提交 merchant_id 会被忽略，归属由服务端写入当前商家。"""
    headers = merchant_headers()
    me = client.get("/users/me", headers=headers).json()["data"]
    response = client.post(
        "/merchant/products",
        json={"name": "ForgeProd", "description": "x", "merchant_id": 999},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["merchant_id"] == me["id"]
    assert data["merchant_id"] != 999


def test_order_amount_and_owner_cannot_be_forged(
    client, auth_headers, merchant_headers
):
    """客户端提交 total_amount / customer_id / merchant_id 不影响服务端计算与归属。"""
    merchant = merchant_headers()
    _, sku = _create_product_with_sku(
        client, merchant, "ForgeOrder", "FO-001", price="50.00", stock=10
    )

    customer = auth_headers(name="Frank", email="frank@example.com")
    response = client.post(
        "/orders",
        json={
            "items": [{"sku_id": sku["id"], "quantity": 2}],
            "total_amount": "0.01",
            "customer_id": 999,
            "merchant_id": 999,
        },
        headers=customer,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_amount"] == "100.00"  # 50 * 2，服务端计算
    assert data["merchant_id"] != 999
    assert data["user_id"] != 999
