import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.models.sku import SKU


class ProductStatus(enum.Enum):
    """商品状态：ON_SALE 在售 / OFF_SALE 下架。"""

    ON_SALE = "on_sale"
    OFF_SALE = "off_sale"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ProductStatus.ON_SALE,
        server_default="on_sale",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    skus: Mapped[list["SKU"]] = relationship(
        "SKU", back_populates="product", cascade="all, delete-orphan"
    )
