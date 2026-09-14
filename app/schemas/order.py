from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.order import OrderStatus


# ---------- 下单请求 ----------
class OrderItemRequest(BaseModel):
    sku_id: int
    quantity: int = Field(..., gt=0)


class OrderCreate(BaseModel):
    """用户下单请求：只提交 SKU 和数量，金额由服务端计算。"""

    items: list[OrderItemRequest] = Field(..., min_length=1)


# ---------- 响应 ----------
class OrderItemResponse(BaseModel):
    id: int
    sku_id: int
    sku_name: str
    unit_price: Decimal
    quantity: int
    subtotal: Decimal

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    id: int
    order_no: str
    user_id: int
    status: OrderStatus
    total_amount: Decimal
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse] = []

    model_config = ConfigDict(from_attributes=True)


class OrderStatusUpdate(BaseModel):
    """管理员修改订单状态。"""

    status: OrderStatus
