"""模拟支付回调接口。

调用方是支付平台（无用户 JWT——这本身是合理的，JWT 面向的是
CUSTOMER/MERCHANT/ADMIN 等终端用户，而支付平台是系统外部调用方）。
但"无 JWT"不等于"无鉴权"：本阶段用共享密钥模拟支付平台身份校验
（X-Mock-Payment-Secret 请求头，值来自服务端 MOCK_PAYMENT_SECRET），
真实生产环境应替换为支付平台提供的数字签名验签机制。
事务、状态机、幂等判断全部在 payment_service 中，Router 只做
鉴权、参数接收与转发。
"""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.database import get_db
from app.exceptions.errors import PaymentAuthError
from app.schemas.common import ResponseModel
from app.schemas.order import OrderResponse
from app.schemas.payment import PaymentCallbackRequest
from app.services import payment_service

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("/callback", response_model=ResponseModel[OrderResponse])
def payment_callback(
    data: PaymentCallbackRequest,
    db: Annotated[Session, Depends(get_db)],
    mock_secret: Annotated[
        str | None,
        Header(alias="X-Mock-Payment-Secret", description="模拟支付平台共享密钥"),
    ] = None,
) -> ResponseModel[OrderResponse]:
    """支付回调：PENDING → PAID，幂等可重入。

    - 鉴权：X-Mock-Payment-Secret 必须与服务端配置一致（401）
    - 首次回调：订单变为 PAID，记录流水号与支付时间
    - 重复回调（同流水号）：幂等返回成功，不重复执行后续业务
    - 已 PAID 但流水号不同 / 已取消订单支付：409
    - status=failed 的通知：仅确认接收，不修改订单状态

    同步 def：FastAPI 自动放线程池执行，避免 FOR UPDATE 阻塞事件循环。
    回调后不发布 MQ 事件（双写一致性问题，第三阶段 Outbox 恢复）。
    """
    # --- 0. 身份校验：先验签，再处理任何回调（含 failed 通知）---
    # 服务端未配置密钥时拒绝所有回调（默认安全姿态，防止裸奔部署）
    if (
        not settings.MOCK_PAYMENT_SECRET
        or mock_secret is None
        or not secrets.compare_digest(mock_secret, settings.MOCK_PAYMENT_SECRET)
    ):
        raise PaymentAuthError()

    if data.status != "success":
        # 支付失败通知不改变订单状态，直接确认接收（避免第三方重试风暴）
        return ResponseModel(data=None, message="回调已接收，订单状态未变更")

    result = payment_service.payment_callback(
        db, data.order_id, data.payment_reference
    )
    return ResponseModel(data=OrderResponse.model_validate(result.order))
