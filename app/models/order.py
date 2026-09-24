import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.models.order_item import OrderItem
    from app.models.user import User


class OrderStatus(enum.Enum):
    """订单状态机（当前 MVP）：

    PENDING ──→ PAID ──→ SHIPPED ──→ COMPLETED
       │
       └──→ CANCELLED

    允许的流转（与 order_service.ALLOWED_STATUS_TRANSITIONS 一致）：
      PENDING   → PAID / CANCELLED
      PAID      → SHIPPED
      SHIPPED   → COMPLETED
      CANCELLED → （终态，不可再流转）
      COMPLETED → （终态，不可再流转）

    注意：PAID 暂不允许取消为 CANCELLED，因为退款流程尚未实现。
    """

    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    SHIPPED = "shipped"
    COMPLETED = "completed"


class Order(Base):
    __tablename__ = "orders"

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
