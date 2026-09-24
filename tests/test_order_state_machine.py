"""订单状态机单元测试：纯逻辑测试，不涉及数据库。

业务规则变化（第二阶段）：
1. 状态机扩展履约链路：PAID→PREPARING→READY→SHIPPED→COMPLETED，
   PAID→SHIPPED 直达被移除（必须经 PREPARING/READY）。
2. 新增角色-动作三维校验（resolve_action）：CUSTOMER/MERCHANT 只能
   执行各自动作表内的动作；ADMIN 无任何状态动作。

验证 TRANSITIONS 表覆盖所有合法/非法流转 + 角色动作权限。
"""

import pytest

from app.exceptions.errors import OrderStatusError, PermissionDeniedError
from app.models.order import OrderStatus
from app.models.user import UserRole
from app.services.order_state_machine import (
    ROLE_ACTIONS,
    TRANSITIONS,
    can_transition,
    resolve_action,
    validate_transition,
)


# ================ 合法转换（基础白名单）================
def test_pending_to_paid_success():
    """PENDING → PAID：合法（支付回调触发）。"""
    assert can_transition(OrderStatus.PENDING, OrderStatus.PAID) is True
    validate_transition(OrderStatus.PENDING, OrderStatus.PAID)  # 不抛异常


def test_pending_to_cancelled_success():
    """PENDING → CANCELLED：合法（客户取消）。"""
    assert can_transition(OrderStatus.PENDING, OrderStatus.CANCELLED) is True
    validate_transition(OrderStatus.PENDING, OrderStatus.CANCELLED)


def test_full_fulfillment_chain_success():
    """完整履约链：PAID→PREPARING→READY→SHIPPED→COMPLETED 逐级合法。"""
    chain = [
        (OrderStatus.PAID, OrderStatus.PREPARING),
        (OrderStatus.PREPARING, OrderStatus.READY),
        (OrderStatus.READY, OrderStatus.SHIPPED),
        (OrderStatus.SHIPPED, OrderStatus.COMPLETED),
    ]
    for current, target in chain:
        assert can_transition(current, target) is True
        validate_transition(current, target)  # 不抛异常


# ================ 非法转换 ================
def test_cancelled_to_paid_fails():
    """CANCELLED → PAID：终态，非法。"""
    assert can_transition(OrderStatus.CANCELLED, OrderStatus.PAID) is False
    with pytest.raises(OrderStatusError) as exc_info:
        validate_transition(OrderStatus.CANCELLED, OrderStatus.PAID)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == 10105


def test_completed_to_pending_fails():
    """COMPLETED → PENDING：终态，非法。"""
    assert can_transition(OrderStatus.COMPLETED, OrderStatus.PENDING) is False
    with pytest.raises(OrderStatusError):
        validate_transition(OrderStatus.COMPLETED, OrderStatus.PENDING)


def test_paid_cannot_be_cancelled():
    """PAID → CANCELLED：退款未实现，暂不允许。"""
    assert can_transition(OrderStatus.PAID, OrderStatus.CANCELLED) is False
    with pytest.raises(OrderStatusError):
        validate_transition(OrderStatus.PAID, OrderStatus.CANCELLED)


def test_paid_to_shipped_fails():
    """业务规则变化：PAID → SHIPPED 不再合法，必须经 PREPARING → READY。"""
    assert can_transition(OrderStatus.PAID, OrderStatus.SHIPPED) is False
    with pytest.raises(OrderStatusError):
        validate_transition(OrderStatus.PAID, OrderStatus.SHIPPED)


def test_pending_cannot_skip_to_delivering():
    """PENDING → SHIPPED：不能跳级（需求示例 PENDING_PAYMENT → DELIVERING）。"""
    assert can_transition(OrderStatus.PENDING, OrderStatus.SHIPPED) is False
    with pytest.raises(OrderStatusError):
        validate_transition(OrderStatus.PENDING, OrderStatus.SHIPPED)


# ================ 终态完整性 ================
def test_terminal_states_have_no_outgoing_transitions():
    """CANCELLED 和 COMPLETED 是终态，不允许任何流出。"""
    assert TRANSITIONS[OrderStatus.CANCELLED] == set()
    assert TRANSITIONS[OrderStatus.COMPLETED] == set()


# ================ 角色动作三维校验（resolve_action）================
def test_customer_can_cancel_pending_order():
    """CUSTOMER：PENDING + cancel → CANCELLED。"""
    target = resolve_action(UserRole.CUSTOMER, "cancel", OrderStatus.PENDING)
    assert target == OrderStatus.CANCELLED


def test_customer_can_confirm_shipped_order():
    """CUSTOMER：SHIPPED + confirm → COMPLETED（确认收货）。"""
    target = resolve_action(UserRole.CUSTOMER, "confirm", OrderStatus.SHIPPED)
    assert target == OrderStatus.COMPLETED


def test_customer_cannot_prepare():
    """CUSTOMER 不能执行 prepare（403 角色权限，非 409 状态冲突）。"""
    with pytest.raises(PermissionDeniedError) as exc_info:
        resolve_action(UserRole.CUSTOMER, "prepare", OrderStatus.PAID)
    assert exc_info.value.status_code == 403


def test_customer_cannot_ship():
    """CUSTOMER 不能执行 ship（403）。"""
    with pytest.raises(PermissionDeniedError):
        resolve_action(UserRole.CUSTOMER, "ship", OrderStatus.READY)


def test_customer_cannot_pay_via_action():
    """CUSTOMER 动作表没有 pay：支付只由回调触发，客户不能直接改 PAID。"""
    assert "pay" not in ROLE_ACTIONS[UserRole.CUSTOMER]
    with pytest.raises(PermissionDeniedError):
        resolve_action(UserRole.CUSTOMER, "pay", OrderStatus.PENDING)


def test_merchant_fulfillment_actions():
    """MERCHANT：prepare/ready/ship 三个动作的目标状态正确。"""
    assert (
        resolve_action(UserRole.MERCHANT, "prepare", OrderStatus.PAID)
        == OrderStatus.PREPARING
    )
    assert (
        resolve_action(UserRole.MERCHANT, "ready", OrderStatus.PREPARING)
        == OrderStatus.READY
    )
    assert (
        resolve_action(UserRole.MERCHANT, "ship", OrderStatus.READY)
        == OrderStatus.SHIPPED
    )


def test_merchant_cannot_confirm_or_cancel():
    """MERCHANT 不能确认收货（confirm）、不能取消订单（cancel）。"""
    with pytest.raises(PermissionDeniedError):
        resolve_action(UserRole.MERCHANT, "confirm", OrderStatus.SHIPPED)
    with pytest.raises(PermissionDeniedError):
        resolve_action(UserRole.MERCHANT, "cancel", OrderStatus.PENDING)


def test_merchant_action_with_wrong_status_409():
    """动作属于该角色但状态不对 → 409（如 PENDING 时调 ship）。"""
    with pytest.raises(OrderStatusError) as exc_info:
        resolve_action(UserRole.MERCHANT, "ship", OrderStatus.PENDING)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == 10105


def test_admin_has_no_actions():
    """ADMIN 不能利用任何动作跳状态（动作表为空）。"""
    assert ROLE_ACTIONS.get(UserRole.ADMIN, {}) == {}
    for action in ("cancel", "confirm", "prepare", "ready", "ship"):
        with pytest.raises(PermissionDeniedError):
            resolve_action(UserRole.ADMIN, action, OrderStatus.PENDING)
