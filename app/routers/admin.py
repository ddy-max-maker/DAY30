"""管理员接口：商品 / SKU / 库存 / 订单管理。

所有接口统一使用 /admin 前缀，需要 ADMIN 角色（require_admin 依赖）。

分页接口示例：

    GET /admin/orders?page=2&page_size=50

非法分页参数（page=0, page_size=0, page_size=101）会由 FastAPI/Pydantic
直接返回 422 校验错误，不会被静默修正。
"""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.auth import require_admin
from app.models.user import User
from app.schemas.common import PageResponse, ResponseModel
from app.schemas.inventory import InventoryResponse, InventoryUpdate
from app.schemas.order import OrderResponse, OrderStatusUpdate
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductStatusUpdate,
    ProductUpdate,
)
from app.schemas.sku import SKUCreate, SKUResponse, SKUUpdate
from app.services import inventory_service, order_service, product_service

router = APIRouter(prefix="/admin", tags=["Admin"])

# ---- 公共依赖：所有 admin 接口都需要 ADMIN 角色 ----
AdminDep = Annotated[User, Depends(require_admin)]
DbDep = Annotated[Session, Depends(get_db)]


# ================ 商品管理 ================
@router.post("/products", response_model=ResponseModel[ProductResponse])
def create_product(
    data: ProductCreate,
    admin: AdminDep,
    db: DbDep,
) -> ResponseModel[ProductResponse]:
    product = product_service.create_product(db, data)
    return ResponseModel(data=ProductResponse.model_validate(product))


@router.put("/products/{product_id}", response_model=ResponseModel[ProductResponse])
def update_product(
    product_id: int,
    data: ProductUpdate,
    admin: AdminDep,
    db: DbDep,
) -> ResponseModel[ProductResponse]:
    product = product_service.update_product(db, product_id, data)
    return ResponseModel(data=ProductResponse.model_validate(product))


@router.patch(
    "/products/{product_id}/status",
    response_model=ResponseModel[ProductResponse],
)
def update_product_status(
    product_id: int,
    data: ProductStatusUpdate,
    admin: AdminDep,
    db: DbDep,
) -> ResponseModel[ProductResponse]:
    product = product_service.update_product_status(db, product_id, data.status)
    return ResponseModel(data=ProductResponse.model_validate(product))


# ================ SKU 管理 ================
@router.post(
    "/products/{product_id}/skus",
    response_model=ResponseModel[SKUResponse],
)
def create_sku(
    product_id: int,
    data: SKUCreate,
    admin: AdminDep,
    db: DbDep,
) -> ResponseModel[SKUResponse]:
    sku = product_service.create_sku(db, product_id, data)
    return ResponseModel(data=SKUResponse.model_validate(sku))


@router.put("/skus/{sku_id}", response_model=ResponseModel[SKUResponse])
def update_sku(
    sku_id: int,
    data: SKUUpdate,
    admin: AdminDep,
    db: DbDep,
) -> ResponseModel[SKUResponse]:
    sku = product_service.update_sku(db, sku_id, data)
    return ResponseModel(data=SKUResponse.model_validate(sku))


# ================ 库存管理 ================
@router.put(
    "/inventory/{sku_id}",
    response_model=ResponseModel[InventoryResponse],
)
def update_inventory(
    sku_id: int,
    data: InventoryUpdate,
    admin: AdminDep,
    db: DbDep,
) -> ResponseModel[InventoryResponse]:
    inventory = inventory_service.update_stock(db, sku_id, data.stock)
    return ResponseModel(data=InventoryResponse.model_validate(inventory))


# ================ 订单管理 ================
@router.get("/orders", response_model=ResponseModel[PageResponse[OrderResponse]])
def list_all_orders(
    admin: AdminDep,
    db: DbDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel[PageResponse[OrderResponse]]:
    """管理员查看全部订单（分页）。"""
    items, total = order_service.get_all_orders_page(db, page=page, page_size=page_size)
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return ResponseModel(
        data=PageResponse(
            items=[OrderResponse.model_validate(o) for o in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
    )


@router.patch(
    "/orders/{order_id}/status",
    response_model=ResponseModel[OrderResponse],
)
def update_order_status(
    order_id: int,
    data: OrderStatusUpdate,
    admin: AdminDep,
    db: DbDep,
) -> ResponseModel[OrderResponse]:
    order = order_service.update_order_status(db, order_id, data)
    return ResponseModel(data=OrderResponse.model_validate(order))
