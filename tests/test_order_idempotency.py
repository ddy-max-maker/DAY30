"""创建订单幂等测试（Idempotency-Key）。

覆盖：
1. 同用户 + 同 key + 同内容 → 返回原订单，库存只扣一次
2. 同用户 + 同 key + 不同内容 → 409 IdempotencyConflictError
3. key 按 user 隔离：不同用户同 key 互不影响
4. 条目顺序不同但内容相同 → 视为同一请求（request_hash 规范化）
5. 不带 key → 保持旧行为（每次都创建新订单）
"""

from app.core.security import create_access_token
from tests.conftest import TestingSessionLocal
from tests.factories.product_factory import create_test_product
from tests.factories.sku_factory import create_test_sku
from tests.factories.user_factory import create_test_merchant, create_test_user


def _setup(stock=10):
    """直接写库准备商家/商品/SKU/消费者，返回 (user_id, sku_id)。

    注意返回普通 ID 而非 ORM 对象：session 关闭后 ORM 对象脱管，
    再访问属性会抛 DetachedInstanceError。
    """
    db = TestingSessionLocal()
    merchant = create_test_merchant(db, email="idem-merchant@example.com")
    product = create_test_product(db, merchant_id=merchant.id, name="IdemProduct")
    sku = create_test_sku(
        db, product_id=product.id, sku_code="IDEM-SKU", price="10.00", stock=stock
    )
    user = create_test_user(db, email="idem-user@example.com")
    user_id, sku_id = user.id, sku.id
    db.close()
    return user_id, sku_id


def _headers(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _post_order(client, user_id: int, sku_id, quantity=2, key=None):
    headers = _headers(user_id)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(
        "/orders",
        json={"items": [{"sku_id": sku_id, "quantity": quantity}]},
        headers=headers,
    )


def _get_stock(sku_id) -> int:
    from sqlalchemy import select

    from app.models.inventory import Inventory

    db = TestingSessionLocal()
    inv = db.scalar(select(Inventory).where(Inventory.sku_id == sku_id))
    db.close()
    return inv.stock


# ================ 1. 幂等命中：同 key 同内容返回原订单 ================
def test_idempotent_hit_returns_same_order(client):
    user_id, sku_id = _setup(stock=10)

    first = _post_order(client, user_id, sku_id, quantity=2, key="idem-abc")
    assert first.status_code == 200
    order1 = first.json()["data"]

    second = _post_order(client, user_id, sku_id, quantity=2, key="idem-abc")
    assert second.status_code == 200
    order2 = second.json()["data"]

    # 返回同一笔订单，库存只扣一次
    assert order1["id"] == order2["id"]
    assert _get_stock(sku_id) == 8  # 10 - 2


# ================ 2. 同 key 不同内容 → 409 ================
def test_idempotency_key_conflict_on_different_content(client):
    user_id, sku_id = _setup(stock=10)

    first = _post_order(client, user_id, sku_id, quantity=2, key="idem-conflict")
    assert first.status_code == 200

    # 同 key 但数量不同 → 409
    response = _post_order(client, user_id, sku_id, quantity=5, key="idem-conflict")
    assert response.status_code == 409
    assert response.json()["code"] == 10108  # IdempotencyConflictError

    # 库存保持第一次扣减后的值
    assert _get_stock(sku_id) == 8


# ================ 3. key 按用户隔离 ================
def test_idempotency_key_is_user_scoped(client):
    db = TestingSessionLocal()
    user_b = create_test_user(db, email="idem-user-b@example.com")
    user_b_id = user_b.id
    db.close()
    user_a_id, sku_id = _setup(stock=100)

    resp_a = _post_order(client, user_a_id, sku_id, quantity=1, key="shared-key")
    resp_b = _post_order(client, user_b_id, sku_id, quantity=1, key="shared-key")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    # 不同用户同 key → 两笔独立订单
    assert resp_a.json()["data"]["id"] != resp_b.json()["data"]["id"]
    assert _get_stock(sku_id) == 98


# ================ 4. 条目顺序不影响幂等判定 ================
def test_request_hash_is_order_insensitive(client):
    db = TestingSessionLocal()
    merchant = create_test_merchant(db, email="idem-m2@example.com")
    product = create_test_product(db, merchant_id=merchant.id, name="IdemP2")
    sku1 = create_test_sku(
        db, product_id=product.id, sku_code="IDEM-A", price="1.00", stock=50
    )
    sku2 = create_test_sku(
        db, product_id=product.id, sku_code="IDEM-B", price="2.00", stock=50
    )
    user = create_test_user(db, email="idem-u2@example.com")
    user_id, sku1_id, sku2_id = user.id, sku1.id, sku2.id
    db.close()

    headers = _headers(user_id) | {"Idempotency-Key": "order-key"}

    # 第一次：A×2 + B×1
    resp1 = client.post(
        "/orders",
        json={
            "items": [
                {"sku_id": sku1_id, "quantity": 2},
                {"sku_id": sku2_id, "quantity": 1},
            ]
        },
        headers=headers,
    )
    assert resp1.status_code == 200

    # 第二次：同样内容但条目顺序颠倒 → 幂等命中，返回原订单
    resp2 = client.post(
        "/orders",
        json={
            "items": [
                {"sku_id": sku2_id, "quantity": 1},
                {"sku_id": sku1_id, "quantity": 2},
            ]
        },
        headers=headers,
    )
    assert resp2.status_code == 200
    assert resp1.json()["data"]["id"] == resp2.json()["data"]["id"]


# ================ 5. 不带 key 保持旧行为 ================
def test_no_key_keeps_legacy_behavior(client):
    user_id, sku_id = _setup(stock=10)

    resp1 = _post_order(client, user_id, sku_id, quantity=2)
    resp2 = _post_order(client, user_id, sku_id, quantity=2)
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # 不带 key：每次都创建新订单、各扣一次库存（旧行为）
    assert resp1.json()["data"]["id"] != resp2.json()["data"]["id"]
    assert _get_stock(sku_id) == 6
