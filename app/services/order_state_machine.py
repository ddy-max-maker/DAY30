"""订单状态机：状态白名单 + 角色-动作三维校验。

状态流转图（第二阶段接入履约链路）：

    PENDING ──→ PAID ──→ PREPARING ──→ READY ──→ SHIPPED ──→ COMPLETED
       │                                             ↑
       └──→ CANCELLED                     （SHIPPED = 配送中/DELIVERING）

终态：CANCELLED、COMPLETED（不可再流转）
PAID 暂不允许取消（退款流程未实现）。

两层校验：
1. TRANSITIONS：纯状态白名单（can_transition / validate_transition），
   供支付回调等"系统触发"的转换使用。
2. ROLE_ACTIONS：角色 + 动作 + 当前状态 → 目标状态（resolve_action），
   供 CUSTOMER / MERCHANT 通过 API 主动发起的动作用。

本模块不涉及数据库操作，不修改订单状态。
Service 层校验通过后自行 commit。
"""

from app.exceptions.errors import OrderStatusError, PermissionDeniedError
from app.models.order import OrderStatus
from app.models.user import UserRole

# 状态转换表：key=当前状态，value=允许流转到的状态集合
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.PREPARING},
    OrderStatus.PREPARING: {OrderStatus.READY},
    OrderStatus.READY: {OrderStatus.SHIPPED},
    OrderStatus.SHIPPED: {OrderStatus.COMPLETED},
    OrderStatus.COMPLETED: set(),
    OrderStatus.CANCELLED: set(),
}

# 角色-动作表：(source_status, target_status)。
# CUSTOMER 的 pay 动作不在此表：支付状态切换只由支付回调触发，
# 客户端不允许通过普通接口把订单改成 PAID。
ROLE_ACTIONS: dict[UserRole, dict[str, tuple[OrderStatus, OrderStatus]]] = {
    UserRole.CUSTOMER: {
        "cancel": (OrderStatus.PENDING, OrderStatus.CANCELLED),
        "confirm": (OrderStatus.SHIPPED, OrderStatus.COMPLETED),
    },
    UserRole.MERCHANT: {
        "prepare": (OrderStatus.PAID, OrderStatus.PREPARING),
        "ready": (OrderStatus.PREPARING, OrderStatus.READY),
        "ship": (OrderStatus.READY, OrderStatus.SHIPPED),
    },
    # ADMIN 本阶段只负责查看订单，不开放任何状态动作（防止万能跳状态）
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


def resolve_action(role: UserRole, action: str, current: OrderStatus) -> OrderStatus:
    """角色 + 动作 + 当前状态 → 目标状态（三维校验）。

    - 动作不属于该角色 → PermissionDeniedError (403)：
      如 CUSTOMER 调 prepare / MERCHANT 确认收货 / ADMIN 任何动作。
    - 动作属于该角色但当前状态不允许 → OrderStatusError (409)：
      如 PENDING 时调 ship、PAID 时取消。
    返回目标状态；最终仍会过一遍 validate_transition，
    保证 ROLE_ACTIONS 与 TRANSITIONS 两张表永不失配。
    """
    allowed = ROLE_ACTIONS.get(role, {}).get(action)
    if allowed is None:
        raise PermissionDeniedError(
            message=f"角色 {role.value} 不允许执行动作 {action}"
        )

    source, target = allowed
    if current != source:
        raise OrderStatusError(
            message=f"订单状态 {current.value} 不允许执行动作 {action}"
        )

    validate_transition(current, target)
    return target
