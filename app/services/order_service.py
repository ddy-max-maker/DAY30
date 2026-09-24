"""订单业务逻辑：下单事务（含幂等）、查询、取消、角色动作。

第二阶段核心事务边界（一个订单 = 一个完整事务，禁止分步 commit）：
  - create_order：校验 + 扣库存 + 建单，任意一步失败全量回滚
  - cancel_order：锁单 + 状态校验 + 恢复库存 + 改状态
  - perform_order_action：锁单 + 三维状态机校验 + 改状态（无库存副作用）
  - payment_callback 见 payment_service
"""

import hashlib
import json
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    CrossMerchantOrderError,
    IdempotencyConflictError,
    OrderNotFoundError,
    SKUNotAvailableError,
    SKUNotFoundError,
)
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import ProductStatus
from app.models.sku import SKU, SKUStatus
from app.models.user import UserRole
from app.schemas.order import OrderCreate, OrderItemRequest
from app.services.inventory_service import deduct_stock, restore_stock
from app.services.order_state_machine import resolve_action, validate_transition

logger = logging.getLogger("backend")


def generate_order_no() -> str:
    """生成业务订单号：ORD + UUID4 的 32 位 hex。

    UUID4 含 122 bit 随机性，碰撞概率可忽略；
    数据库 order_no 的 unique 约束作为最终兜底。
    """
    return f"ORD{uuid.uuid4().hex}"


def compute_request_hash(items: list[OrderItemRequest]) -> str:
    """规范化订单请求并计算 SHA-256 指纹（幂等用）。

    规范化规则：按 sku_id 排序后的 (sku_id, quantity) 序列。
    不直接 hash dict 的字符串表示（Python dict 无序，不稳定）。
    同一订单内容无论条目顺序如何，hash 恒定。
    """
    normalized = sorted((item.sku_id, item.quantity) for item in items)
    payload = json.dumps(normalized, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ================ 创建订单 ================
def create_order(
    db: Session, user_id: int, data: OrderCreate, idempotency_key: str | None = None
) -> Order:
    """下单核心流程（单事务，任意一步失败全量回滚）。

    流程：
      1. 幂等预检：同用户 + 同 key → hash 相同返回原订单，不同抛 409
      2. 一次查询所有 SKU，校验存在 + 可销售 + 单商家订单
      3. 按 sku_id 升序逐个条件原子 UPDATE 扣库存
         （统一加锁顺序，显著降低多 SKU 并发下单时
         因锁获取顺序不同导致的死锁概率）
      4. 用下单时的 SKU 名称和价格创建快照 OrderItem
      5. 服务端累加计算 total_amount（绝不信任客户端）
      6. 创建 Order（归属唯一商家），一次性 commit

    幂等：
      - Header 不传 key：保持旧行为，不做幂等
      - 传 key：先查 (user_id, key)，并发场景由
        UNIQUE(user_id, idempotency_key) 兜底（IntegrityError 后回查）
    """
    request_hash = compute_request_hash(data.items)

    # --- 1. 幂等预检（非并发路径）---
    if idempotency_key:
        existing = _find_idempotent_order(db, user_id, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError()
            logger.info(
                "idempotency_hit order_id=%s user_id=%s",
                existing.id,
                user_id,
            )
            return existing

    try:
        # --- 2. 一次查询所有 SKU（避免 N+1），逐项校验 ---
        sku_ids = [item.sku_id for item in data.items]
        skus: dict[int, SKU] = {
            sku.id: sku
            for sku in db.scalars(select(SKU).where(SKU.id.in_(sku_ids))).all()
        }

        order_items: list[OrderItem] = []
        total_amount = Decimal("0")
        merchant_ids: set[int] = set()

        for item_request in data.items:
            sku = skus.get(item_request.sku_id)
            if sku is None:
                raise SKUNotFoundError(message=f"SKU(id={item_request.sku_id}) 不存在")

            # 校验可销售（SKU 和 Product 状态都要检查）
            if sku.status != SKUStatus.ACTIVE:
                raise SKUNotAvailableError(message=f"SKU {sku.sku_code} 已停售")
            if sku.product is None or sku.product.status != ProductStatus.ON_SALE:
                raise SKUNotAvailableError(
                    message=f"商品 {sku.product.name if sku.product else ''} 已下架"
                )

            merchant_ids.add(sku.product.merchant_id)
            subtotal = sku.price * item_request.quantity
            total_amount += subtotal
            order_items.append(
                OrderItem(
                    sku_id=sku.id,
                    sku_name=sku.name,
                    unit_price=sku.price,
                    quantity=item_request.quantity,
                    subtotal=subtotal,
                )
            )

        # 单商家订单校验：跨商家下单直接拒绝（不拆单）
        if len(merchant_ids) > 1:
            raise CrossMerchantOrderError()

        # --- 3. 按 sku_id 升序条件原子扣库存（统一加锁顺序，降低死锁概率）---
        for item_request in sorted(data.items, key=lambda i: i.sku_id):
            deduct_stock(db, item_request.sku_id, item_request.quantity)

        # --- 4. 创建订单 + 关联订单项（归属唯一商家）---
        order = Order(
            order_no=generate_order_no(),
            user_id=user_id,
            merchant_id=merchant_ids.pop(),
            status=OrderStatus.PENDING,
            total_amount=total_amount,
            idempotency_key=idempotency_key,
            request_hash=request_hash if idempotency_key else None,
        )
        for item in order_items:
            order.items.append(item)

        db.add(order)
        db.commit()
        db.refresh(order)
        logger.info(
            "order_created order_id=%s user_id=%s total_amount=%s",
            order.id,
            user_id,
            order.total_amount,
        )
        return order

    except IntegrityError:
        # UNIQUE(user_id, idempotency_key) 冲突：并发重复请求的兜底。
        # 两个同 key 请求同时通过预检时，只有一个 INSERT 成功，
        # 另一个在这里被唯一约束挡住，回查后按 hash 决定返回原订单或 409。
        db.rollback()
        if idempotency_key:
            existing = _find_idempotent_order(db, user_id, idempotency_key)
            if existing is not None and existing.request_hash == request_hash:
                logger.info(
                    "idempotency_hit order_id=%s user_id=%s (concurrent)",
                    existing.id,
                    user_id,
                )
                return existing
            raise IdempotencyConflictError() from None
        # 无 key 时的 IntegrityError（order_no 撞唯一约束等极小概率事件），原样抛出
        raise
    except Exception:
        db.rollback()
        raise


def _find_idempotent_order(
    db: Session, user_id: int, idempotency_key: str
) -> Order | None:
    return db.scalar(
        select(Order).where(
            Order.user_id == user_id, Order.idempotency_key == idempotency_key
        )
    )


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
    """CUSTOMER 取消自己的订单（仅 PENDING 可取消），并恢复库存。

    并发安全：先 SELECT ... FOR UPDATE 锁定订单行，再检查状态。
    幂等取消：拿到锁后若订单已是 CANCELLED，直接返回当前订单
    （重复点击取消/并发取消不会重复恢复库存）。
    """
    try:
        # --- 1. 锁定订单行（必须在检查状态之前）---
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise OrderNotFoundError()

        if order.user_id != user_id:
            raise OrderNotFoundError(message="订单不存在")

        # --- 2. 幂等取消：已 CANCELLED 直接返回，不重复恢复库存 ---
        if order.status == OrderStatus.CANCELLED:
            logger.info("order_cancel_idempotent order_id=%s", order_id)
            return order

        # --- 3. 状态机校验（拿到锁之后读到的一定是最新值）---
        validate_transition(order.status, OrderStatus.CANCELLED)

        # --- 4. 恢复库存（条件原子 UPDATE）---
        for item in order.items:
            restore_stock(db, item.sku_id, item.quantity)

        order.status = OrderStatus.CANCELLED
        db.commit()
        db.refresh(order)
        logger.info("order_cancelled order_id=%s user_id=%s", order_id, user_id)
        return order
    except Exception:
        db.rollback()
        raise


# ================ 角色订单动作（备货/发货/确认收货）================
def perform_order_action(
    db: Session, order_id: int, user_id: int, role: UserRole, action: str
) -> Order:
    """CUSTOMER / MERCHANT 通过 API 发起的订单状态动作（无库存副作用）。

    三维校验：角色（动作是否属于该角色）+ 状态（当前状态是否允许）
    + 动作（resolve_action 内部实现），非法动作 403、非法状态 409。

    并发安全：FOR UPDATE 锁定订单行后再校验，防止并发动作读到旧状态。
    """
    try:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise OrderNotFoundError()

        # 归属校验：越权返回 404（不泄露订单存在性）
        if role == UserRole.CUSTOMER and order.user_id != user_id:
            raise OrderNotFoundError(message="订单不存在")
        if role == UserRole.MERCHANT and order.merchant_id != user_id:
            raise OrderNotFoundError(message="订单不存在")

        target = resolve_action(role, action, order.status)
        order.status = target
        db.commit()
        db.refresh(order)
        logger.info(
            "order_action order_id=%s role=%s action=%s -> %s",
            order_id,
            role.value,
            action,
            order.status.value,
        )
        return order
    except Exception:
        db.rollback()
        raise
