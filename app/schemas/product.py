from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.product import ProductStatus
from app.models.sku import SKUStatus


# ---------- 管理员用 ----------
class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None


class ProductStatusUpdate(BaseModel):
    status: ProductStatus


# ---------- 响应 ----------
class ProductResponse(BaseModel):
    id: int
    name: str
    description: str | None
    status: ProductStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SKUBriefResponse(BaseModel):
    """SKU 简要信息（嵌套在商品详情中返回给用户）。"""

    id: int
    sku_code: str
    name: str
    price: Decimal
    status: SKUStatus

    model_config = ConfigDict(from_attributes=True)


class ProductDetailResponse(BaseModel):
    """商品详情（含 SKU 列表），用于普通用户浏览。"""

    id: int
    name: str
    description: str | None
    status: ProductStatus
    skus: list[SKUBriefResponse] = []

    model_config = ConfigDict(from_attributes=True)
