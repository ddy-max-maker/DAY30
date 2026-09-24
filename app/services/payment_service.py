"""支付业务逻辑：模拟支付回调（第二阶段）。

不接入真实支付网关，目的只是验证：支付状态机 + 回调幂等 + 并发竞态。

回调处理规则：
  1. 首次回调：PENDING → PAID，记录 payment_reference + paid_at，
     事务提交后发布 order.paid 事件
  2. 重复回调（同 reference）：幂等返回成功，不重复执行后续业务
  3. 冲突回调（已 PAID 但 reference 不同）：409 PaymentConflictError
  4. 已取消订单不能再支付：409 OrderStatusError

并发安全：SELECT ... FOR UPDATE 锁定订单行后再校验状态，
支付与取消同时到达时串行化，最终只产生一种合法结果。
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    OrderNotFoundError,
    PaymentConflictError,
)
from app.models.order import Order, OrderStatus
from app.mq.publisher import publish_order_paid_event
from app.services.order_state_machine import validate_transition

logger = logging.getLogger("backend")


@dataclass
class PaymentCallbackResult:
    """回调结果：transitioned 标记本次是否真正发生了 PENDING → PAID。

    重复回调 transitioned=False，调用方据此跳过 MQ 发布等后续业务。
    """

    order: Order
    transitioned: bool


def payment_callback(
    db: Session, order_id: int, payment_reference: str
) -> PaymentCallbackResult:
    """处理支付回调（单事务）。

    流程：
      BEGIN
        → SELECT Order FOR UPDATE（锁行，串行化支付与取消竞态）
        → 检查当前状态与 payment_reference
        → PENDING → PAID（状态机校验）
        → 记录 payment_reference / paid_at
      COMMIT

    幂等：
      - 已 PAID 且 reference 相同 → 直接返回成功（不重复发 MQ）
      - 已 PAID 但 reference 不同 → 409（同一订单绑定多个流水号）
    """
    try:
        # --- 1. 锁定订单行 ---
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise OrderNotFoundError()

        # --- 2. 幂等：重复回调（同流水号）直接返回成功 ---
        if (
            order.status == OrderStatus.PAID
            and order.payment_reference == payment_reference
        ):
            logger.info("duplicate_payment_callback order_id=%s", order_id)
            return PaymentCallbackResult(order=order, transitioned=False)

        # --- 3. 冲突：已 PAID 但流水号不同 ---
        if order.status == OrderStatus.PAID:
            raise PaymentConflictError(
                message="订单已支付，且支付流水号与本次回调不一致"
            )

        # --- 4. 状态机校验（仅 PENDING → PAID 合法；CANCELLED 等终态被拒）---
        validate_transition(order.status, OrderStatus.PAID)

        # --- 5. 更新支付状态 ---
        order.status = OrderStatus.PAID
        order.payment_reference = payment_reference
        order.paid_at = datetime.now()
        db.commit()
        db.refresh(order)
        logger.info(
            "payment_success order_id=%s order_no=%s", order_id, order.order_no
        )
        return PaymentCallbackResult(order=order, transitioned=True)
    except Exception:
        db.rollback()
        raise


async def payment_callback_and_publish(
    db: Session, order_id: int, payment_reference: str
) -> PaymentCallbackResult:
    """支付回调，并在事务提交成功且为首次支付时发布 order.paid 事件。

    与原 pay_order_and_publish 相同的两段式设计：
    - DB 部分同步 SQLAlchemy（含 FOR UPDATE），放线程池执行；
    - MQ 部分全异步，只在 transitioned=True（首次支付）时发送，
      重复回调不重复发布事件。
    MQ 发送失败只记录 error 日志，不影响支付结果。
    """
    result = await run_in_threadpool(payment_callback, db, order_id, payment_reference)

    if result.transitioned:
        try:
            await publish_order_paid_event(result.order.id, result.order.user_id)
        except Exception:
            logger.error(
                "publish_order_paid_failed order_id=%s user_id=%s",
                result.order.id,
                result.order.user_id,
                exc_info=True,
            )

    return result
