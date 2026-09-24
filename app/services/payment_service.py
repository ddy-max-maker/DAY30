"""支付业务逻辑：模拟支付回调（第二阶段）。

不接入真实支付网关，目的只是验证：支付状态机 + 回调幂等 + 并发竞态。

回调处理规则：
  1. 首次回调：PENDING → PAID，记录 payment_reference + paid_at
  2. 重复回调（同 reference）：幂等返回成功，不重复执行后续业务
  3. 冲突回调（已 PAID 但 reference 不同）：409 PaymentConflictError
  4. 已取消订单不能再支付：409 OrderStatusError

并发安全：SELECT ... FOR UPDATE 锁定订单行后再校验状态，
支付与取消同时到达时串行化，最终只产生一种合法结果。

注意：本阶段回调后不再直接发布 order.paid MQ 事件。"MySQL 事务提交
后再 publish"存在双写一致性问题（commit 成功但 publish 失败时，
数据库已支付而下游永远收不到事件）。MQ 发布将在第三阶段以
Transactional Outbox（订单 + 事件表同事务提交 + 独立 Publisher）
方式恢复，transitioned 标记即为 Outbox 写入的判定依据。
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions.errors import (
    OrderNotFoundError,
    PaymentConflictError,
)
from app.models.order import Order, OrderStatus
from app.services.order_state_machine import validate_transition

logger = logging.getLogger("backend")


@dataclass
class PaymentCallbackResult:
    """回调结果：transitioned 标记本次是否真正发生了 PENDING → PAID。

    重复回调 transitioned=False。第三阶段 Outbox 将据此决定是否写入事件表。
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
      - 已 PAID 且 reference 相同 → 直接返回成功（不重复执行后续业务）
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
