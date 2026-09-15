"""订单状态机单元测试：纯逻辑测试，不涉及数据库。

验证 TRANSITIONS 表覆盖所有合法/非法流转。
"""

import pytest

from app.exceptions.errors import OrderStatusError
from app.models.order import OrderStatus
from app.services.order_state_machine import (
    TRANSITIONS,
    can_transition,
    validate_transition,
)


# ================ 合法转换 ================
def test_pending_to_paid_success():
    """PENDING → PAID：合法。"""
    assert can_transition(OrderStatus.PENDING, OrderStatus.PAID) is True
    validate_transition(OrderStatus.PENDING, OrderStatus.PAID)  # 不抛异常


def test_pending_to_cancelled_success():
    """PENDING → CANCELLED：合法。"""
    assert can_transition(OrderStatus.PENDING, OrderStatus.CANCELLED) is True
    validate_transition(OrderStatus.PENDING, OrderStatus.CANCELLED)


def test_paid_to_shipped_success():
    """PAID → SHIPPED：合法。"""
    assert can_transition(OrderStatus.PAID, OrderStatus.SHIPPED) is True
    validate_transition(OrderStatus.PAID, OrderStatus.SHIPPED)


def test_shipped_to_completed_success():
    """SHIPPED → COMPLETED：合法。"""
    assert can_transition(OrderStatus.SHIPPED, OrderStatus.COMPLETED) is True
    validate_transition(OrderStatus.SHIPPED, OrderStatus.COMPLETED)


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
    with pytest.raises(OrderStatusError) as exc_info:
        validate_transition(OrderStatus.COMPLETED, OrderStatus.PENDING)
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == 10105


# ================ 补充：终态完整性 ================
def test_terminal_states_have_no_outgoing_transitions():
    """CANCELLED 和 COMPLETED 是终态，不允许任何流出。"""
    assert TRANSITIONS[OrderStatus.CANCELLED] == set()
    assert TRANSITIONS[OrderStatus.COMPLETED] == set()


def test_paid_cannot_be_cancelled():
    """PAID → CANCELLED：退款未实现，暂不允许。"""
    assert can_transition(OrderStatus.PAID, OrderStatus.CANCELLED) is False
    with pytest.raises(OrderStatusError):
        validate_transition(OrderStatus.PAID, OrderStatus.CANCELLED)
