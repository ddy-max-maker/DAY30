"""模拟支付回调接口。

由模拟支付平台调用（无 JWT 鉴权；真实场景用签名验签，本阶段模拟实现）。
事务、状态机、幂等判断全部在 payment_service 中，Router 只做参数接收与转发。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.schemas.common import ResponseModel
from app.schemas.order import OrderResponse
from app.schemas.payment import PaymentCallbackRequest
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("/callback", response_model=ResponseModel[OrderResponse])
async def payment_callback(
    data: PaymentCallbackRequest,
    db: Annotated[Session, Depends(get_db)],
) -> ResponseModel[OrderResponse]:
    """支付回调：PENDING → PAID，幂等可重入。

    - 首次回调：订单变为 PAID，记录流水号与支付时间，发布 order.paid 事件
    - 重复回调（同流水号）：幂等返回成功，不重复执行后续业务
    - 已 PAID 但流水号不同 / 已取消订单支付：409
    - status=failed 的通知：仅确认接收，不修改订单状态
    """
    if data.status != "success":
        # 支付失败通知不改变订单状态，直接确认接收（避免第三方重试风暴）
        return ResponseModel(data=None, message="回调已接收，订单状态未变更")

    result = await payment_service.payment_callback_and_publish(
        db, data.order_id, data.payment_reference
    )
    return ResponseModel(data=OrderResponse.model_validate(result.order))
