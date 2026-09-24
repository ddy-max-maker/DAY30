import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.models.order_item import OrderItem
    from app.models.user import User


class OrderStatus(enum.Enum):
    """订单状态机（第二阶段：接入履约链路）：

    PENDING ──→ PAID ──→ PREPARING ──→ READY ──→ SHIPPED ──→ COMPLETED
       │                                           ↑
       └──→ CANCELLED                    （SHIPPED = 配送中/DELIVERING）

    允许的流转（与 order_state_machine.TRANSITIONS 一致）：
      PENDING   → PAID（支付回调）/ CANCELLED（CUSTOMER 取消）
      PAID      → PREPARING（MERCHANT 备货）
      PREPARING → READY（MERCHANT 备货完成）
      READY     → SHIPPED（MERCHANT 发货）
      SHIPPED   → COMPLETED（CUSTOMER 确认收货）
      CANCELLED / COMPLETED → 终态，不可再流转

    命名兼容说明：PENDING 即"待支付"（PENDING_PAYMENT），
    SHIPPED 即"配送中"（DELIVERING），保留现有数据库枚举值不做改名迁移。
    """

    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    PREPARING = "preparing"
    READY = "ready"


class Order(Base):
    __tablename__ = "orders"
    # 幂等键唯一约束：同一用户 + 同一 Idempotency-Key 只允许一个订单。
    # MySQL 唯一索引对 NULL 不生效，旧订单（无 key）不受影响。
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_orders_user_idempotency_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # UUID 业务订单号格式为 ORD + 32 位 hex（共 35 字符），列宽留余量到 64
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # 订单归属商家：下单时取订单内商品的归属商家写入（当前版本限定单商家订单，
    # 跨商家下单返回 409）。存量数据由 Alembic 迁移通过
    # order_items -> skus -> products 链路回填。
    merchant_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=OrderStatus.PENDING,
        server_default="pending",
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    # ---- 幂等字段（创建订单）----
    # 客户端 Header Idempotency-Key：同一次下单动作重试必须携带同一个 key。
    # request_hash：规范化后 (sku_id, quantity) 序列的 SHA-256，
    # 用于拦截"同 key 不同内容"的请求（返回 409）。
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ---- 支付字段（模拟支付回调）----
    # 支付平台流水号：同一流水号只能绑定一个订单（UNIQUE），
    # NULL 兼容未支付订单（MySQL 唯一索引不约束 NULL）。
    payment_reference: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # user_id 与 merchant_id 两个外键都指向 users.id，
    # 必须显式指定 foreign_keys，否则 SQLAlchemy 无法消除歧义
    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="orders"
    )
    merchant: Mapped["User"] = relationship("User", foreign_keys=[merchant_id])
    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )
