from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.sku import SKUStatus


class SKUCreate(BaseModel):
    sku_code: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    price: Decimal = Field(..., gt=0)
    stock: int = Field(..., ge=0)
    status: SKUStatus = SKUStatus.ACTIVE


class SKUUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    price: Optional[Decimal] = Field(None, gt=0)
    status: Optional[SKUStatus] = None


class SKUResponse(BaseModel):
    id: int
    product_id: int
    sku_code: str
    name: str
    price: Decimal
    status: SKUStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
