"""库存业务逻辑。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    InsufficientStockError,
    PermissionDeniedError,
    SKUNotFoundError,
)
from app.models.inventory import Inventory


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
    """下单扣减库存。

    使用 SELECT ... FOR UPDATE（行级悲观锁）防止超卖：
    两个并发请求同时扣同一 SKU 的库存时，数据库保证只有一个能拿到锁，
    另一个必须等前者提交后才能读到最新的 stock 值。

    注意：此函数必须在调用方的事务内执行，不要自行 commit。
    """

    inventory = db.scalar(
        select(Inventory)
        .where(Inventory.sku_id == sku_id)
        .with_for_update()  # 行级锁：SELECT ... FOR UPDATE
    )
    if inventory is None:
        raise SKUNotFoundError(message="SKU 库存记录不存在")

    if inventory.stock < quantity:
        raise InsufficientStockError(
            message=f"库存不足：当前 {inventory.stock}，需要 {quantity}"
        )

    inventory.stock -= quantity
    inventory.version += 1
    return inventory


def restore_stock(db: Session, sku_id: int, quantity: int) -> Inventory:
    """取消订单时恢复库存。同样在调用方事务内执行。"""

    inventory = db.scalar(
        select(Inventory).where(Inventory.sku_id == sku_id).with_for_update()
    )
    if inventory is None:
        raise SKUNotFoundError(message="SKU 库存记录不存在")

    inventory.stock += quantity
    inventory.version += 1
    return inventory
