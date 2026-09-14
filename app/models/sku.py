import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.models.inventory import Inventory
    from app.models.product import Product


class SKUStatus(enum.Enum):
    """SKU 状态：ACTIVE 可销售 / INACTIVE 停售。"""

    ACTIVE = "active"
    INACTIVE = "inactive"


class SKU(Base):
    __tablename__ = "skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    sku_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    status: Mapped[SKUStatus] = mapped_column(
        Enum(SKUStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=SKUStatus.ACTIVE,
        server_default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    product: Mapped["Product"] = relationship("Product", back_populates="skus")
    inventory: Mapped["Inventory | None"] = relationship(
        "Inventory", back_populates="sku", uselist=False, cascade="all, delete-orphan"
    )
