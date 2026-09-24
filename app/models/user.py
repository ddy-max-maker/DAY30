import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.models.order import Order


class UserRole(enum.Enum):
    """用户角色枚举（三角色 RBAC）。

    CUSTOMER = 消费者：注册接口创建，可浏览商品、下单、用购物车。
    MERCHANT = 商家：内部方式创建（不开放公开注册），可管理自己的商品与订单。
    ADMIN    = 管理员：脚本/初始化创建，平台运营视角。

    历史兼容：原 USER 角色通过 Alembic 迁移平滑改值为 CUSTOMER。
    """

    CUSTOMER = "customer"
    MERCHANT = "merchant"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=UserRole.CUSTOMER,
        server_default="customer",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # 乐观锁版本号：每次更新 +1，UPDATE 时带上 WHERE version = ?
    # server_default 保证存量数据在 ALTER TABLE 时自动填 1
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # Order 有 user_id / merchant_id 两个外键指向 users.id，
    # 反向关系必须显式指定 foreign_keys，否则 SQLAlchemy 无法消除歧义
    orders: Mapped[list["Order"]] = relationship(
        "Order", foreign_keys="Order.user_id", back_populates="user"
    )
