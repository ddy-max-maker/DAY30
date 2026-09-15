"""普通用户订单接口：下单、查询、取消。"""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.auth import get_current_user
from app.exceptions.errors import OrderNotFoundError
from app.models.user import User
from app.schemas.common import PageResponse, ResponseModel
from app.schemas.order import OrderCreate, OrderResponse
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("", response_model=ResponseModel[OrderResponse])
def create_order(
    order_data: OrderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[OrderResponse]:
    """下单：客户端只提交 sku_id + quantity，金额由服务端计算。"""
    order = order_service.create_order(db, current_user.id, order_data)
    return ResponseModel(data=OrderResponse.model_validate(order))


@router.get("", response_model=ResponseModel[PageResponse[OrderResponse]])
def list_my_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel[PageResponse[OrderResponse]]:
    """查看自己的全部订单（分页）。"""
    items, total = order_service.get_orders_by_user_page(
        db, current_user.id, page=page, page_size=page_size
    )
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


@router.get("/{order_id}", response_model=ResponseModel[OrderResponse])
def get_my_order(
    order_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[OrderResponse]:
    """查看自己的订单详情（越权访问返回 404 而非 403，避免泄露订单存在性）。"""
    order = order_service.get_order_by_id(db, order_id)

    if order is None or order.user_id != current_user.id:
        raise OrderNotFoundError()

    return ResponseModel(data=OrderResponse.model_validate(order))


@router.post("/{order_id}/cancel", response_model=ResponseModel[OrderResponse])
def cancel_my_order(
    order_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[OrderResponse]:
    """取消自己的订单（仅 PENDING 状态可取消）。"""
    order = order_service.cancel_order(db, order_id, current_user.id)
    return ResponseModel(data=OrderResponse.model_validate(order))
