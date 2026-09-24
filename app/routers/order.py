"""消费者订单接口：下单（支持幂等）、查询、取消、确认收货。

仅 CUSTOMER 可访问（require_customer）：MERCHANT/ADMIN 不是订单消费角色。
支付不在此处：支付状态切换只由支付回调（/payments/callback）触发。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.dependencies.permissions import require_customer
from app.exceptions.errors import OrderNotFoundError
from app.models.user import User
from app.schemas.common import ResponseModel
from app.schemas.order import OrderCreate, OrderResponse
from app.services import order_service

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("", response_model=ResponseModel[OrderResponse])
def create_order(
    order_data: OrderCreate,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "幂等键：同一次下单动作重试必须携带同一个 key（可选，<=64 字符）"
            ),
            max_length=64,
        ),
    ] = None,
) -> ResponseModel[OrderResponse]:
    """下单：客户端只提交 sku_id + quantity，金额由服务端计算。

    携带 Idempotency-Key 时启用幂等保护：
    同用户同 key 同内容 → 返回原订单；同 key 不同内容 → 409。
    """
    order = order_service.create_order(
        db, current_user.id, order_data, idempotency_key=idempotency_key
    )
    return ResponseModel(data=OrderResponse.model_validate(order))


@router.get("", response_model=ResponseModel[list[OrderResponse]])
def list_my_orders(
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[list[OrderResponse]]:
    """查看自己的全部订单。"""
    orders = order_service.get_orders_by_user(db, current_user.id)
    return ResponseModel(data=[OrderResponse.model_validate(o) for o in orders])


@router.get("/{order_id}", response_model=ResponseModel[OrderResponse])
def get_my_order(
    order_id: int,
    current_user: Annotated[User, Depends(require_customer)],
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
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[OrderResponse]:
    """取消自己的订单（仅 PENDING 可取消，重复取消幂等返回原订单）。"""
    order = order_service.cancel_order(db, order_id, current_user.id)
    return ResponseModel(data=OrderResponse.model_validate(order))


@router.post("/{order_id}/confirm", response_model=ResponseModel[OrderResponse])
def confirm_my_order(
    order_id: int,
    current_user: Annotated[User, Depends(require_customer)],
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[OrderResponse]:
    """确认收货（仅 SHIPPED → COMPLETED）。"""
    order = order_service.perform_order_action(
        db, order_id, current_user.id, current_user.role, "confirm"
    )
    return ResponseModel(data=OrderResponse.model_validate(order))
