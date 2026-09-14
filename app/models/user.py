import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.todo import Todo


class UserRole(enum.Enum):
    """用户角色枚举。

    RBAC 基础：USER = 普通用户，ADMIN = 管理员。
    注册接口只能创建 USER，ADMIN 通过脚本/初始化创建。
    """

    USER = "user"
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
        default=UserRole.USER,
        server_default="user",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    # 乐观锁版本号：每次更新 +1，UPDATE 时带上 WHERE version = ?
    # server_default 保证存量数据在 ALTER TABLE 时自动填 1
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    todos: Mapped[list["Todo"]] = relationship("Todo", back_populates="user")
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="user")
