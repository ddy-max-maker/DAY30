"""购物车业务逻辑：Redis Hash 存储，MySQL 关联 SKU 信息。

Redis Key 设计：
    cart:{user_id}    -> Hash{ sku_id: quantity }

只存 sku_id + quantity，不缓存 name/price/stock，
每次查询实时关联 MySQL SKU 表，保证价格最新。
"""

import logging

import redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.redis import redis_client
from app.exceptions.errors import CartError, SKUNotFoundError
from app.models.sku import SKU

logger = logging.getLogger("backend")

CART_KEY_PREFIX = "cart"


def _cart_key(user_id: int) -> str:
    """构造购物车 Redis Key。"""
    return f"{CART_KEY_PREFIX}:{user_id}"


# ================ 添加 / 修改 ================
def add_item(user_id: int, sku_id: int, quantity: int, db: Session) -> None:
    """添加商品到购物车（HSET 天然幂等：重复添加即覆盖数量）。

    校验 SKU 存在性：不存在抛 SKUNotFoundError (404)。
    """
    sku = db.get(SKU, sku_id)
    if sku is None:
        raise SKUNotFoundError(message=f"SKU(id={sku_id}) 不存在")

    try:
        redis_client.hset(_cart_key(user_id), str(sku_id), quantity)
    except redis.RedisError:
        logger.exception("cart_hset_failed user_id=%s sku_id=%s", user_id, sku_id)
        raise CartError() from None


def update_item(user_id: int, sku_id: int, quantity: int) -> None:
    """修改购物车中某 SKU 的数量（HSET 覆盖）。

    不校验 SKU 存在性（添加时已校验）；若 SKU 已从 MySQL 删除，
    get_cart 会自动过滤掉。
    """
    try:
        redis_client.hset(_cart_key(user_id), str(sku_id), quantity)
    except redis.RedisError:
        logger.exception("cart_hset_failed user_id=%s sku_id=%s", user_id, sku_id)
        raise CartError() from None


# ================ 删除 ================
def remove_item(user_id: int, sku_id: int) -> None:
    """从购物车删除某 SKU（HDEL 幂等，不存在也不报错）。"""
    try:
        redis_client.hdel(_cart_key(user_id), str(sku_id))
    except redis.RedisError:
        logger.exception("cart_hdel_failed user_id=%s sku_id=%s", user_id, sku_id)
        raise CartError() from None


# ================ 查询 ================
def get_cart(user_id: int, db: Session) -> list[dict]:
    """查询购物车：Redis 读 sku_id+quantity，MySQL 关联 name+price。

    返回 [{sku_id, name, price, quantity}, ...]，
    SKU 已从 MySQL 删除的项会被自动过滤。
    """
    try:
        raw = redis_client.hgetall(_cart_key(user_id))
    except redis.RedisError:
        logger.exception("cart_hgetall_failed user_id=%s", user_id)
        raise CartError() from None

    if not raw:
        return []

    # Redis key 是 str，转成 int 查 MySQL
    sku_ids = [int(k) for k in raw]
    skus = db.scalars(select(SKU).where(SKU.id.in_(sku_ids))).all()
    sku_map = {s.id: s for s in skus}

    items: list[dict] = []
    for sku_id_str, qty_str in raw.items():
        sku = sku_map.get(int(sku_id_str))
        if sku is None:
            # SKU 已删除，跳过（不返回脏数据）
            continue
        items.append(
            {
                "sku_id": sku.id,
                "name": sku.name,
                "price": sku.price,
                "quantity": int(qty_str),
            }
        )
    return items


# ================ 清空 ================
def clear_cart(user_id: int) -> None:
    """清空购物车（下单后可调用，MVP 暂不自动触发）。"""
    try:
        redis_client.delete(_cart_key(user_id))
    except redis.RedisError:
        logger.exception("cart_delete_failed user_id=%s", user_id)
        raise CartError() from None
