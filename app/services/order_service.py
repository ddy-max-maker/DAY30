"""订单业务逻辑：下单事务、查询、取消、支付。"""

import logging
import uuid
from decimal import Decimal

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    CrossMerchantOrderError,
    OrderNotFoundError,
    SKUNotAvailableError,
    SKUNotFoundError,
)
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import ProductStatus
from app.models.sku import SKU, SKUStatus
from app.mq.publisher import publish_order_paid_event
from app.schemas.order import OrderCreate, OrderStatusUpdate
from app.services.inventory_service import deduct_stock, restore_stock
from app.services.order_state_machine import validate_transition

logger = logging.getLogger("backend")


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
      2. 收集商品归属商家，校验单商家订单（跨商家直接拒绝）
      3. 扣减库存（SELECT FOR UPDATE 行级锁防超卖）
      4. 用下单时的 SKU 名称和价格创建快照 OrderItem
      5. 服务端累加计算 total_amount
      6. 创建 Order（归属唯一商家），一次性 commit

    事务边界：整个函数是一个事务。任意一步抛异常 → rollback，
    已扣的库存、已创建的 OrderItem 全部回滚。
    """
    order_items: list[OrderItem] = []
    total_amount = Decimal("0")
    merchant_ids: set[int] = set()  # 收集订单内所有商品的归属商家，用于单商家校验

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

            # --- 3. 单商家订单校验：收集归属商家，跨商家下单直接拒绝（不拆单）---
            merchant_ids.add(sku.product.merchant_id)

            # --- 4. 扣减库存（行级锁，同一事务内）---
            # deduct_stock 内部用 SELECT ... FOR UPDATE，
            # 并发下单时第二个请求会阻塞，直到前一个事务提交
            deduct_stock(db, sku.id, item_request.quantity)

            # --- 5. 计算小计（服务端定价，绝不信任客户端）---
            subtotal = sku.price * item_request.quantity
            total_amount += subtotal

            # --- 6. 创建快照 OrderItem ---
            order_items.append(
                OrderItem(
                    sku_id=sku.id,
                    sku_name=sku.name,
                    unit_price=sku.price,
                    quantity=item_request.quantity,
                    subtotal=subtotal,
                )
            )

        # --- 7. 跨商家校验：一个订单只允许购买同一商家的商品 ---
        if len(merchant_ids) > 1:
            raise CrossMerchantOrderError()

        # --- 8. 创建订单 + 关联订单项（归属唯一商家）---
        order = Order(
            order_no=generate_order_no(),
            user_id=user_id,
            merchant_id=merchant_ids.pop(),
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


def get_orders_by_merchant(db: Session, merchant_id: int) -> list[Order]:
    """商家查看包含自己商品的订单（按订单归属商家过滤）。"""
    return list(
        db.scalars(
            select(Order)
            .where(Order.merchant_id == merchant_id)
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

        # --- 2. 状态机校验（拿到锁之后读到的一定是最新值）---
        validate_transition(order.status, OrderStatus.CANCELLED)

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
    validate_transition(order.status, data.status)

    order.status = data.status
    db.commit()
    db.refresh(order)
    return order


# ================ 用户支付订单 ================
def pay_order(db: Session, order_id: int, user_id: int) -> Order:
    """用户支付自己的订单（PENDING → PAID）。

    模拟支付：不接入真实支付网关，只做状态流转。

    并发安全：先 SELECT ... FOR UPDATE 锁定订单行，再校验状态，
    防止并发重复支付。两个并发支付请求中，第二个会阻塞在订单行锁上，
    等第一个提交后才能读到最新状态（PAID），从而被状态机挡住。
    """
    try:
        # --- 1. 锁定订单行 ---
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise OrderNotFoundError()

        if order.user_id != user_id:
            raise OrderNotFoundError(message="订单不存在")

        # --- 2. 状态机校验（只允许 PENDING → PAID）---
        validate_transition(order.status, OrderStatus.PAID)

        # --- 3. 更新状态 ---
        order.status = OrderStatus.PAID
        db.commit()
        db.refresh(order)
        return order
    except Exception:
        db.rollback()
        raise


async def pay_order_and_publish(db: Session, order_id: int, user_id: int) -> Order:
    """支付订单，并在数据库事务提交成功后发布 order.paid 事件。

    为什么拆成"同步事务 + 异步发消息"两段：
    - DB 部分使用同步 SQLAlchemy（含 SELECT ... FOR UPDATE 行锁），
      通过 run_in_threadpool 放到线程池，避免阻塞 asyncio 事件循环；
    - MQ 部分使用 aio-pika 全异步，必须在 commit 之后才发送，
      杜绝"消息已消费但事务回滚"的不一致。

    MQ 发送失败只记录 error 日志，不影响支付结果：
    DB 事务与 MQ 不是同一事务（本阶段不实现 Outbox/事务消息）。
    """
    order = await run_in_threadpool(pay_order, db, order_id, user_id)

    try:
        await publish_order_paid_event(order.id, order.user_id)
    except Exception:
        logger.error(
            "publish_order_paid_failed order_id=%s user_id=%s",
            order.id,
            order.user_id,
            exc_info=True,
        )

    return order
