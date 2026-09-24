"""商家接口：商品 / SKU / 库存 / 订单管理。

所有接口统一使用 /merchant 前缀，需要 MERCHANT 角色（require_merchant 依赖）。
资源归属校验在 service 层完成：商家只能操作 merchant_id 等于自己的资源。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.permissions import require_merchant
from app.exceptions.errors import OrderNotFoundError
from app.models.user import User
from app.schemas.common import ResponseModel
from app.schemas.inventory import InventoryResponse, InventoryUpdate
from app.schemas.order import OrderResponse
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductStatusUpdate,
    ProductUpdate,
)
from app.schemas.sku import SKUCreate, SKUResponse, SKUUpdate
from app.services import inventory_service, order_service, product_service

router = APIRouter(prefix="/merchant", tags=["Merchant"])

MerchantDep = Annotated[User, Depends(require_merchant)]
DbDep = Annotated[Session, Depends(get_db)]


# ================ 商品管理 ================
@router.post("/products", response_model=ResponseModel[ProductResponse])
def create_product(
    data: ProductCreate,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[ProductResponse]:
    """创建商品：merchant_id 由服务端根据当前登录商家写入。"""
    product = product_service.create_product(db, data, merchant_id=merchant.id)
    return ResponseModel(data=ProductResponse.model_validate(product))


@router.get("/products", response_model=ResponseModel[list[ProductResponse]])
def list_my_products(
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[list[ProductResponse]]:
    """查看自己的商品列表（含下架商品）。"""
    products = product_service.get_products_by_merchant(db, merchant.id)
    return ResponseModel(data=[ProductResponse.model_validate(p) for p in products])


@router.put("/products/{product_id}", response_model=ResponseModel[ProductResponse])
def update_product(
    product_id: int,
    data: ProductUpdate,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[ProductResponse]:
    """修改自己的商品（非本人商品返回 403）。"""
    product = product_service.update_product(
        db, product_id, data, merchant_id=merchant.id
    )
    return ResponseModel(data=ProductResponse.model_validate(product))


@router.patch(
    "/products/{product_id}/status",
    response_model=ResponseModel[ProductResponse],
)
def update_product_status(
    product_id: int,
    data: ProductStatusUpdate,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[ProductResponse]:
    """上架/下架自己的商品（商家删除商品即下架，避免破坏订单快照外键）。"""
    product = product_service.update_product_status(
        db, product_id, data.status, merchant_id=merchant.id
    )
    return ResponseModel(data=ProductResponse.model_validate(product))


# ================ SKU / 库存管理 ================
@router.post(
    "/products/{product_id}/skus",
    response_model=ResponseModel[SKUResponse],
)
def create_sku(
    product_id: int,
    data: SKUCreate,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[SKUResponse]:
    """为自己的商品创建 SKU（含初始库存）。"""
    sku = product_service.create_sku(db, product_id, data, merchant_id=merchant.id)
    return ResponseModel(data=SKUResponse.model_validate(sku))


@router.put("/skus/{sku_id}", response_model=ResponseModel[SKUResponse])
def update_sku(
    sku_id: int,
    data: SKUUpdate,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[SKUResponse]:
    """修改自己的 SKU 信息（名称/价格/状态）。"""
    sku = product_service.update_sku(db, sku_id, data, merchant_id=merchant.id)
    return ResponseModel(data=SKUResponse.model_validate(sku))


@router.put("/inventory/{sku_id}", response_model=ResponseModel[InventoryResponse])
def update_inventory(
    sku_id: int,
    data: InventoryUpdate,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[InventoryResponse]:
    """修改自己商品的库存（非本人商品返回 403）。"""
    inventory = inventory_service.update_stock(
        db, sku_id, data.stock, merchant_id=merchant.id
    )
    return ResponseModel(data=InventoryResponse.model_validate(inventory))


# ================ 订单管理 ================
@router.get("/orders", response_model=ResponseModel[list[OrderResponse]])
def list_my_orders(
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[list[OrderResponse]]:
    """查看包含自己商品的订单。"""
    orders = order_service.get_orders_by_merchant(db, merchant.id)
    return ResponseModel(data=[OrderResponse.model_validate(o) for o in orders])


@router.get("/orders/{order_id}", response_model=ResponseModel[OrderResponse])
def get_my_order(
    order_id: int,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[OrderResponse]:
    """查看属于自己的订单详情（非本人订单返回 404，不泄露存在性）。"""
    order = order_service.get_order_by_id(db, order_id)

    if order is None or order.merchant_id != merchant.id:
        raise OrderNotFoundError()

    return ResponseModel(data=OrderResponse.model_validate(order))


# ================ 订单履约动作（PAID → PREPARING → READY → SHIPPED）================
@router.post("/orders/{order_id}/prepare", response_model=ResponseModel[OrderResponse])
def prepare_order(
    order_id: int,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[OrderResponse]:
    """备货（仅 PAID → PREPARING，非本人订单返回 404）。"""
    order = order_service.perform_order_action(
        db, order_id, merchant.id, merchant.role, "prepare"
    )
    return ResponseModel(data=OrderResponse.model_validate(order))


@router.post("/orders/{order_id}/ready", response_model=ResponseModel[OrderResponse])
def ready_order(
    order_id: int,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[OrderResponse]:
    """备货完成（仅 PREPARING → READY）。"""
    order = order_service.perform_order_action(
        db, order_id, merchant.id, merchant.role, "ready"
    )
    return ResponseModel(data=OrderResponse.model_validate(order))


@router.post("/orders/{order_id}/ship", response_model=ResponseModel[OrderResponse])
def ship_order(
    order_id: int,
    merchant: MerchantDep,
    db: DbDep,
) -> ResponseModel[OrderResponse]:
    """发货（仅 READY → SHIPPED，即进入配送中）。"""
    order = order_service.perform_order_action(
        db, order_id, merchant.id, merchant.role, "ship"
    )
    return ResponseModel(data=OrderResponse.model_validate(order))
