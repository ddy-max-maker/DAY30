"""购物车模块测试：Redis Hash 存储、用户隔离、SKU 校验。

使用 factory + db_session 直接写库准备 SKU 数据，
通过 API 验证购物车行为，确保测试隔离、不依赖已有 id。

覆盖：
1. 登录用户添加购物车成功
2. 查询购物车成功
3. 修改数量成功
4. 删除商品成功
5. 多个 SKU 购物车
6. 不存在 SKU 添加失败 404
7. 未登录访问购物车 401
8. 用户之间购物车隔离
"""

from app.core.security import create_access_token
from tests.factories.product_factory import create_test_product
from tests.factories.sku_factory import create_test_sku
from tests.factories.user_factory import create_test_merchant, create_test_user


def _auth(token: str) -> dict:
    """把 token 字符串包装成 Authorization header。"""
    return {"Authorization": f"Bearer {token}"}


def _create_sku(db_session, sku_code="CART-SKU-1", price="99.00", stock=100) -> int:
    """用 factory 直接写库创建 merchant + product + sku，返回 sku_id。"""
    # 商品归属商家（三角色体系后 Product.merchant_id 非空）；
    # 用 sku_code 拼邮箱避免同一测试内多次创建商家时邮箱冲突
    merchant = create_test_merchant(
        db_session, email=f"merchant-{sku_code}@example.com"
    )
    product = create_test_product(
        db_session, merchant_id=merchant.id, name=f"Product-{sku_code}"
    )
    sku = create_test_sku(
        db_session,
        product_id=product.id,
        sku_code=sku_code,
        name=f"{sku_code} name",
        price=price,
        stock=stock,
    )
    return sku.id


# ================ 1. 添加购物车成功 ================
def test_add_item_success(client, user_token, db_session):
    """登录用户添加 SKU 到购物车 → 200，返回含该项的购物车。"""
    sku_id = _create_sku(db_session)

    response = client.post(
        "/cart/items",
        json={"sku_id": sku_id, "quantity": 2},
        headers=_auth(user_token),
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0

    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["sku_id"] == sku_id
    assert items[0]["name"] == "CART-SKU-1 name"
    assert items[0]["price"] == "99.00"
    assert items[0]["quantity"] == 2


# ================ 2. 查询购物车成功 ================
def test_get_cart_success(client, user_token, db_session):
    """先添加再查询，返回的购物车含已添加的 SKU。"""
    sku_id = _create_sku(db_session, sku_code="GET-SKU")

    client.post(
        "/cart/items",
        json={"sku_id": sku_id, "quantity": 3},
        headers=_auth(user_token),
    )

    response = client.get("/cart", headers=_auth(user_token))
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["sku_id"] == sku_id
    assert items[0]["quantity"] == 3


# ================ 3. 修改数量成功 ================
def test_update_quantity_success(client, user_token, db_session):
    """PUT 修改购物车中某 SKU 的数量。"""
    sku_id = _create_sku(db_session, sku_code="UPD-SKU")

    client.post(
        "/cart/items",
        json={"sku_id": sku_id, "quantity": 1},
        headers=_auth(user_token),
    )

    response = client.put(
        f"/cart/items/{sku_id}",
        json={"quantity": 5},
        headers=_auth(user_token),
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert items[0]["quantity"] == 5


# ================ 4. 删除商品成功 ================
def test_remove_item_success(client, user_token, db_session):
    """DELETE 删除购物车中某 SKU，购物车变空。"""
    sku_id = _create_sku(db_session, sku_code="DEL-SKU")

    client.post(
        "/cart/items",
        json={"sku_id": sku_id, "quantity": 2},
        headers=_auth(user_token),
    )

    response = client.delete(
        f"/cart/items/{sku_id}",
        headers=_auth(user_token),
    )
    assert response.status_code == 200
    assert len(response.json()["data"]["items"]) == 0


# ================ 5. 多个 SKU 购物车 ================
def test_multiple_skus_in_cart(client, user_token, db_session):
    """购物车可以同时包含多个不同 SKU。"""
    sku1 = _create_sku(db_session, sku_code="MULTI-1")
    sku2 = _create_sku(db_session, sku_code="MULTI-2")

    client.post(
        "/cart/items",
        json={"sku_id": sku1, "quantity": 1},
        headers=_auth(user_token),
    )
    client.post(
        "/cart/items",
        json={"sku_id": sku2, "quantity": 2},
        headers=_auth(user_token),
    )

    response = client.get("/cart", headers=_auth(user_token))
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 2

    quantities = {item["sku_id"]: item["quantity"] for item in items}
    assert quantities[sku1] == 1
    assert quantities[sku2] == 2


# ================ 6. 不存在 SKU 添加失败 404 ================
def test_add_nonexistent_sku_fails_404(client, user_token, db_session):
    """添加不存在的 SKU 到购物车 → SKUNotFoundError (404)。"""
    response = client.post(
        "/cart/items",
        json={"sku_id": 99999, "quantity": 1},
        headers=_auth(user_token),
    )
    assert response.status_code == 404
    assert response.json()["code"] == 10101  # SKUNotFoundError


# ================ 7. 未登录访问购物车 401 ================
def test_unauthenticated_access_401(client):
    """未提供 Authorization 头访问购物车 → 401。"""
    response = client.get("/cart")
    assert response.status_code == 401
    assert response.json()["code"] == 10001  # TokenInvalidError


# ================ 8. 用户之间购物车隔离 ================
def test_cart_isolation_between_users(client, user_token, db_session):
    """用户 A 和用户 B 的购物车互不可见（Redis Key 按 user_id 隔离）。"""
    sku_id = _create_sku(db_session, sku_code="ISO-SKU")

    # 用户 A 添加 5 个
    client.post(
        "/cart/items",
        json={"sku_id": sku_id, "quantity": 5},
        headers=_auth(user_token),
    )

    # 创建用户 B
    user_b = create_test_user(db_session, name="UserB", email="user_b@example.com")
    token_b = create_access_token(user_b.id)

    # 用户 B 查询购物车 → 应为空
    response_b = client.get("/cart", headers=_auth(token_b))
    assert response_b.status_code == 200
    assert len(response_b.json()["data"]["items"]) == 0

    # 用户 B 添加 1 个
    client.post(
        "/cart/items",
        json={"sku_id": sku_id, "quantity": 1},
        headers=_auth(token_b),
    )

    # 用户 A 购物车仍是 5
    response_a = client.get("/cart", headers=_auth(user_token))
    assert response_a.json()["data"]["items"][0]["quantity"] == 5

    # 用户 B 购物车是 1
    response_b = client.get("/cart", headers=_auth(token_b))
    assert response_b.json()["data"]["items"][0]["quantity"] == 1
