from pydantic import BaseModel, Field


class PaymentCallbackRequest(BaseModel):
    """模拟支付回调请求（由模拟支付平台调用，无 JWT 鉴权）。

    真实场景第三方回调使用签名验签，本阶段为模拟实现不做验签，
    后续接入真实支付时补充。
    """

    order_id: int
    payment_reference: str = Field(..., min_length=1, max_length=64)
    status: str = Field(default="success", pattern="^(success|failed)$")
