from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InventoryUpdate(BaseModel):
    """管理员调整库存：直接设置新的库存数量。"""

    stock: int = Field(..., ge=0)


class InventoryResponse(BaseModel):
    id: int
    sku_id: int
    stock: int
    version: int
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
