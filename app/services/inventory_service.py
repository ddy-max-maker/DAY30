"""库存业务逻辑。

扣减/恢复库存采用 MySQL 条件原子 UPDATE（第二阶段设计决策）：

    UPDATE inventories
    SET stock = stock - :quantity
    WHERE sku_id = :sku_id AND stock >= :quantity

- 单条 SQL 原子完成"检查 + 扣减"，不存在"先读后写"的并发窗口；
- rowcount == 0 表示库存不足（记录存在性已提前校验），直接 409；
- 相比 SELECT FOR UPDATE + ORM 赋值：锁持有时间更短，
  且从根上杜绝"两个事务都读到旧 stock 再相减"的超卖路径。

选择 MySQL 而非 Redis 分布式锁的理由：
1. MySQL 本身就是库存最终数据源，单库内 UPDATE 天然一致；
2. 避免 Redis/MySQL 双写一致性这一额外复杂度；
3. 当前为单体订单系统，分布式锁属于过早优化（后续高并发专题再议）。
"""

import logging

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    InsufficientStockError,
    PermissionDeniedError,
    SKUNotFoundError,
)
from app.models.inventory import Inventory

logger = logging.getLogger("backend")


def get_inventory_by_sku(db: Session, sku_id: int) -> Inventory | None:
    return db.scalar(select(Inventory).where(Inventory.sku_id == sku_id))


def update_stock(
    db: Session, sku_id: int, stock: int, merchant_id: int | None = None
) -> Inventory:
    """直接设置库存数量。

    merchant_id 非 None 时校验归属（商家只能修改自己商品的库存），
    None 表示管理员操作（不校验归属）。
    """

    inventory = get_inventory_by_sku(db, sku_id)
    if inventory is None:
        raise SKUNotFoundError(message="SKU 库存记录不存在")
    if merchant_id is not None:
        if inventory.sku.product.merchant_id != merchant_id:
            raise PermissionDeniedError(message="无权操作其他商家的库存")

    inventory.stock = stock
    inventory.version += 1
    db.commit()
    db.refresh(inventory)
    return inventory


def deduct_stock(db: Session, sku_id: int, quantity: int) -> Inventory:
    """下单扣减库存：条件原子 UPDATE，rowcount 判断成败。

    WHERE stock >= quantity 不满足时 rowcount == 0 → InsufficientStockError。
    记录不存在（无库存行）→ SKUNotFoundError，与库存不足区分开。

    注意：此函数必须在调用方的事务内执行，不要自行 commit。
    """

    # 先无锁读确认库存行存在，用于把 404（记录缺失）和 409（库存不足）区分开
    inventory = get_inventory_by_sku(db, sku_id)
    if inventory is None:
        raise SKUNotFoundError(message="SKU 库存记录不存在")

    # 条件原子 UPDATE：单语句完成"检查 + 扣减"，天然防超卖
    result = db.execute(
        update(Inventory)
        .where(Inventory.sku_id == sku_id, Inventory.stock >= quantity)
        .values(stock=Inventory.stock - quantity, version=Inventory.version + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        logger.error(
            "inventory_insufficient sku_id=%s requested=%s", sku_id, quantity
        )
        raise InsufficientStockError(
            message=f"库存不足：需要 {quantity}，当前可用不足"
        )

    # 失效 ORM 缓存并刷新，让调用方拿到 UPDATE 后的最新值（同事务内可见）
    db.refresh(inventory)
    return inventory


def restore_stock(db: Session, sku_id: int, quantity: int) -> Inventory:
    """取消订单时恢复库存：原子 UPDATE（stock = stock + quantity）。

    取消恢复不需要条件判断（恢复总是允许的），
    rowcount == 0 仅在库存行不存在时发生。
    同样在调用方事务内执行，不自行 commit。
    """

    inventory = get_inventory_by_sku(db, sku_id)
    if inventory is None:
        raise SKUNotFoundError(message="SKU 库存记录不存在")

    db.execute(
        update(Inventory)
        .where(Inventory.sku_id == sku_id)
        .values(stock=Inventory.stock + quantity, version=Inventory.version + 1)
        .execution_options(synchronize_session=False)
    )
    db.refresh(inventory)
    return inventory
