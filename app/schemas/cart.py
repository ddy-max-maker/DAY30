"""购物车请求 / 响应模型。

购物车数据存 Redis，不落 MySQL，因此没有 ORM model。
Schema 直接定义给 Router 层使用。
"""

from decimal import Decimal

from pydantic import BaseModel, Field


class CartItemCreate(BaseModel):
    """添加购物车请求体。"""

    sku_id: int = Field(..., gt=0)
    quantity: int = Field(..., ge=1)


class CartItemUpdate(BaseModel):
    """修改购物车数量请求体（sku_id 从 path 取）。"""

    quantity: int = Field(..., ge=1)


class CartItemResponse(BaseModel):
    """购物车单项：SKU 快照信息 + 数量。

    name / price 来自 MySQL SKU 表（购物车不缓存这些字段，
    每次查询实时关联，避免价格/名称不同步）。
    """

    sku_id: int
    name: str
    price: Decimal
    quantity: int


class CartResponse(BaseModel):
    """购物车整体响应。"""

    items: list[CartItemResponse] = []
