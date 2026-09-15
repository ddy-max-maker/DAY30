"""订单状态机：只负责判断状态转换是否合法。

状态流转图：

    PENDING ──→ PAID ──→ SHIPPED ──→ COMPLETED
       │
       └──→ CANCELLED

终态：CANCELLED、COMPLETED（不可再流转）
PAID 暂不允许取消（退款流程未实现）。

本模块不涉及数据库操作，不修改订单状态，
只提供查询（can_transition）和校验（validate_transition）。
Service 层调用 validate_transition 后自行 commit。
"""

from app.exceptions.errors import OrderStatusError
from app.models.order import OrderStatus

# 状态转换表：key=当前状态，value=允许流转到的状态集合
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED},
    OrderStatus.SHIPPED: {OrderStatus.COMPLETED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELLED: set(),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    """判断从 current 到 target 的状态转换是否合法。"""
    return target in TRANSITIONS.get(current, set())


def validate_transition(current: OrderStatus, target: OrderStatus) -> None:
    """校验状态转换，非法则抛出 OrderStatusError (HTTP 409)。

    在 Service 层调用，通常在锁定订单行（SELECT ... FOR UPDATE）
    之后、修改状态之前调用，确保读到的是最新状态。
    """
    if not can_transition(current, target):
        raise OrderStatusError(
            message=f"订单状态不允许从 {current.value} 变更为 {target.value}"
        )
