"""购物车接口：添加 / 修改 / 删除 / 查询。

仅 CUSTOMER 可用购物车（require_customer）：MERCHANT/ADMIN 不是消费场景角色。
所有 Redis 操作在 cart_service 层完成，Router 不直接接触 Redis。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.permissions import require_customer
from app.models.user import User
from app.schemas.cart import (
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
)
from app.schemas.common import ResponseModel
from app.services import cart_service

router = APIRouter(prefix="/cart", tags=["Cart"])

CustomerDep = Annotated[User, Depends(require_customer)]
DbDep = Annotated[Session, Depends(get_db)]


def _build_cart(user_id: int, db: Session) -> CartResponse:
    """从 service 取原始数据，构建 CartResponse。"""
    items = cart_service.get_cart(user_id, db)
    return CartResponse(items=[CartItemResponse(**i) for i in items])


@router.post("/items", response_model=ResponseModel[CartResponse])
def add_cart_item(
    item: CartItemCreate,
    current_user: CustomerDep,
    db: DbDep,
) -> ResponseModel[CartResponse]:
    """添加商品到购物车。SKU 不存在返回 404。"""
    cart_service.add_item(current_user.id, item.sku_id, item.quantity, db)
    return ResponseModel(data=_build_cart(current_user.id, db))


@router.put("/items/{sku_id}", response_model=ResponseModel[CartResponse])
def update_cart_item(
    sku_id: int,
    item: CartItemUpdate,
    current_user: CustomerDep,
    db: DbDep,
) -> ResponseModel[CartResponse]:
    """修改购物车中某 SKU 的数量（quantity >= 1）。"""
    cart_service.update_item(current_user.id, sku_id, item.quantity)
    return ResponseModel(data=_build_cart(current_user.id, db))


@router.delete("/items/{sku_id}", response_model=ResponseModel[CartResponse])
def remove_cart_item(
    sku_id: int,
    current_user: CustomerDep,
    db: DbDep,
) -> ResponseModel[CartResponse]:
    """从购物车删除某 SKU。"""
    cart_service.remove_item(current_user.id, sku_id)
    return ResponseModel(data=_build_cart(current_user.id, db))


@router.get("", response_model=ResponseModel[CartResponse])
def get_cart(
    current_user: CustomerDep,
    db: DbDep,
) -> ResponseModel[CartResponse]:
    """查看当前用户购物车（实时关联 MySQL SKU 信息）。"""
    return ResponseModel(data=_build_cart(current_user.id, db))
