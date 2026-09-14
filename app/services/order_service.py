"""订单业务逻辑：下单事务、查询、取消。"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    OrderNotFoundError,
    OrderStatusError,
    SKUNotAvailableError,
    SKUNotFoundError,
)
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import ProductStatus
from app.models.sku import SKU, SKUStatus
from app.schemas.order import OrderCreate, OrderStatusUpdate
from app.services.inventory_service import deduct_stock, restore_stock

# 管理员订单状态机：key 为当前状态，value 为允许流转到的状态集合。
# CANCELLED / COMPLETED 是终态；PAID 暂不允许取消（退款功能未实现）。
ALLOWED_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED},
    OrderStatus.SHIPPED: {OrderStatus.COMPLETED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.COMPLETED: set(),
}


def generate_order_no() -> str:
    """生成业务订单号：ORD + UUID4 的 32 位 hex。

    UUID4 含 122 bit 随机性，碰撞概率可忽略；
    数据库 order_no 的 unique 约束作为最终兜底。
    """
    return f"ORD{uuid.uuid4().hex}"


# ================ 创建订单 ================
def create_order(db: Session, user_id: int, data: OrderCreate) -> Order:
    """下单核心流程（单事务，任意一步失败全量回滚）。

    流程：
      1. 遍历订单项，逐个校验 SKU 存在 + 可销售 + 库存足够
      2. 扣减库存（SELECT FOR UPDATE 行级锁防超卖）
      3. 用下单时的 SKU 名称和价格创建快照 OrderItem
      4. 服务端累加计算 total_amount
      5. 创建 Order，一次性 commit

    事务边界：整个函数是一个事务。任意一步抛异常 → rollback，
    已扣的库存、已创建的 OrderItem 全部回滚。
    """
    order_items: list[OrderItem] = []
    total_amount = Decimal("0")

    try:
        for item_request in data.items:
            # --- 1. 校验 SKU 存在 ---
            sku = db.scalar(select(SKU).where(SKU.id == item_request.sku_id))
            if sku is None:
                raise SKUNotFoundError(message=f"SKU(id={item_request.sku_id}) 不存在")

            # --- 2. 校验可销售（SKU 和 Product 状态都要检查）---
            if sku.status != SKUStatus.ACTIVE:
                raise SKUNotAvailableError(message=f"SKU {sku.sku_code} 已停售")
            if sku.product is None or sku.product.status != ProductStatus.ON_SALE:
                raise SKUNotAvailableError(
                    message=f"商品 {sku.product.name if sku.product else ''} 已下架"
                )

            # --- 3. 扣减库存（行级锁，同一事务内）---
            # deduct_stock 内部用 SELECT ... FOR UPDATE，
            # 并发下单时第二个请求会阻塞，直到前一个事务提交
            deduct_stock(db, sku.id, item_request.quantity)

            # --- 4. 计算小计（服务端定价，绝不信任客户端）---
            subtotal = sku.price * item_request.quantity
            total_amount += subtotal

            # --- 5. 创建快照 OrderItem ---
            order_items.append(
                OrderItem(
                    sku_id=sku.id,
                    sku_name=sku.name,
                    unit_price=sku.price,
                    quantity=item_request.quantity,
                    subtotal=subtotal,
                )
            )

        # --- 6. 创建订单 + 关联订单项 ---
        order = Order(
            order_no=generate_order_no(),
            user_id=user_id,
            status=OrderStatus.PENDING,
            total_amount=total_amount,
        )
        for item in order_items:
            order.items.append(item)

        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    except Exception:
        db.rollback()
        raise


# ================ 查询订单 ================
def get_orders_by_user(db: Session, user_id: int) -> list[Order]:
    return list(
        db.scalars(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
        ).all()
    )


def get_order_by_id(db: Session, order_id: int) -> Order | None:
    return db.get(Order, order_id)


def get_all_orders(db: Session) -> list[Order]:
    return list(db.scalars(select(Order).order_by(Order.created_at.desc())).all())


# ================ 取消订单 ================
def cancel_order(db: Session, order_id: int, user_id: int) -> Order:
    """用户取消自己的订单。

    只允许取消 PENDING 状态的订单（未支付）。
    取消时恢复库存（同一事务，保证一致）。

    并发安全：先 SELECT ... FOR UPDATE 锁定订单行，再检查状态。
    两个并发取消请求中，第二个会阻塞在订单行锁上，等第一个提交后
    才能读到最新状态（CANCELLED），从而被状态检查挡住，
    不会出现库存被重复恢复。
    """
    try:
        # --- 1. 锁定订单行（必须在检查状态之前）---
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise OrderNotFoundError()

        if order.user_id != user_id:
            raise OrderNotFoundError(message="订单不存在")

        # --- 2. 拿到锁之后再检查状态（此时读到的一定是最新值）---
        if order.status != OrderStatus.PENDING:
            raise OrderStatusError(
                message=f"当前订单状态为 {order.status.value}，无法取消"
            )

        # --- 3. 恢复库存（每行库存也单独加 FOR UPDATE 锁）---
        for item in order.items:
            restore_stock(db, item.sku_id, item.quantity)

        order.status = OrderStatus.CANCELLED
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


# ================ 管理员修改订单状态 ================
def update_order_status(db: Session, order_id: int, data: OrderStatusUpdate) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise OrderNotFoundError()

    # 状态机校验：只允许白名单内的流转，终态订单拒绝任何修改
    if data.status not in ALLOWED_STATUS_TRANSITIONS[order.status]:
        raise OrderStatusError(
            message=f"订单状态不允许从 {order.status.value} 变更为 {data.status.value}"
        )

    order.status = data.status
    db.commit()
    db.refresh(order)
    return order
