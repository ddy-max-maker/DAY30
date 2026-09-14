from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.models.sku import SKU


class Inventory(Base):
    """库存表：一个 SKU 对应一条库存记录。

    version 字段预留给后续乐观锁扩展（当前阶段用行级锁保证安全）。
    """

    __tablename__ = "inventories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("skus.id"), nullable=False, unique=True
    )
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sku: Mapped["SKU"] = relationship("SKU", back_populates="inventory")
