"""订单交易链路：状态机扩展 + 幂等/支付字段

- orders.status 枚举扩展：新增 preparing / ready（履约链路 PAID → PREPARING → READY → SHIPPED）
- 新增幂等字段：idempotency_key / request_hash（创建订单幂等）
- 新增支付字段：payment_reference / paid_at（模拟支付回调）
- 唯一约束：UNIQUE(user_id, idempotency_key)、UNIQUE(payment_reference)
  （MySQL 唯一索引不约束 NULL，旧订单/未支付订单不受影响）

Revision ID: c9e4f7a1b2d3
Revises: b7c3d9e2f4a1
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c9e4f7a1b2d3"
down_revision = "b7c3d9e2f4a1"
branch_labels = None
depends_on = None

# 扩展前后的订单状态枚举（MySQL 原生 ENUM，扩展新增值是安全的在线操作）
OLD_STATUS = ("pending", "paid", "cancelled", "shipped", "completed")
NEW_STATUS = ("pending", "paid", "cancelled", "shipped", "completed", "preparing", "ready")


def upgrade() -> None:
    # 1. 状态枚举扩展（MODIFY COLUMN）
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.Enum(*OLD_STATUS, name="orderstatus"),
        type_=sa.Enum(*NEW_STATUS, name="orderstatus"),
        existing_nullable=False,
        existing_server_default="pending",
    )

    # 2. 幂等字段
    op.add_column("orders", sa.Column("idempotency_key", sa.String(64), nullable=True))
    op.add_column("orders", sa.Column("request_hash", sa.String(64), nullable=True))

    # 3. 支付字段
    op.add_column(
        "orders", sa.Column("payment_reference", sa.String(64), nullable=True)
    )
    op.add_column("orders", sa.Column("paid_at", sa.DateTime(), nullable=True))

    # 4. 唯一约束（同一用户同一幂等键只能一个订单；流水号全局唯一）
    op.create_unique_constraint(
        "uq_orders_user_idempotency_key", "orders", ["user_id", "idempotency_key"]
    )
    op.create_unique_constraint(
        "uq_orders_payment_reference", "orders", ["payment_reference"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_orders_payment_reference", "orders", type_="unique")
    op.drop_constraint("uq_orders_user_idempotency_key", "orders", type_="unique")
    op.drop_column("orders", "paid_at")
    op.drop_column("orders", "payment_reference")
    op.drop_column("orders", "request_hash")
    op.drop_column("orders", "idempotency_key")
    op.alter_column(
        "orders",
        "status",
        existing_type=sa.Enum(*NEW_STATUS, name="orderstatus"),
        type_=sa.Enum(*OLD_STATUS, name="orderstatus"),
        existing_nullable=False,
        existing_server_default="pending",
    )
