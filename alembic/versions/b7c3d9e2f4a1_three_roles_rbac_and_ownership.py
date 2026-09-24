"""three roles rbac and resource ownership

Revision ID: b7c3d9e2f4a1
Revises: 25fd58b31845
Create Date: 2026-09-23

三角色 RBAC + 资源归属迁移（禁止删库重建，全部为增量变更）：

1. users.role: ENUM('user','admin') → ENUM('customer','merchant','admin')
   三步走：先扩展枚举（新旧值并存）→ UPDATE 存量 user → customer →
   收缩枚举。MySQL 的 ENUM 修改用 MODIFY COLUMN，每步都必须显式带上
   NOT NULL / DEFAULT，否则会丢失列属性。

2. products.merchant_id: 加列(nullable) → 回填存量 → 索引 + NOT NULL。
   回填策略：优先归属最早的 MERCHANT；若无 MERCHANT 用户（历史数据
   只有 ADMIN），兜底归属最早的 ADMIN（平台自营语义）。空库时无行受影响。

3. orders.merchant_id: 加列(nullable) → 通过 order_items → skus →
   products 链路回填 → 索引 + NOT NULL。假设历史订单均有订单项
   （业务约束保证）；若存在无订单项的脏数据，回填后会因 NULL 违反
   NOT NULL 而失败，需先人工清理脏数据再重跑。

升级后请用以下 SQL 验证（不要只看 alembic upgrade 成功）：
   SHOW COLUMNS FROM users LIKE 'role';
   SELECT role, COUNT(*) FROM users GROUP BY role;      -- 不应出现 'user'
   SELECT COUNT(*) FROM products WHERE merchant_id IS NULL;  -- 应为 0
   SELECT COUNT(*) FROM orders   WHERE merchant_id IS NULL;  -- 应为 0
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c3d9e2f4a1"
down_revision: Union[str, Sequence[str], None] = "25fd58b31845"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------- 1. users.role 三角色迁移 ----------
    # 1a. 先扩展枚举值集合（新旧值并存，保证 UPDATE 期间两种值都合法）
    op.alter_column(
        "users",
        "role",
        existing_type=sa.Enum("user", "admin", name="userrole"),
        type_=sa.Enum("user", "customer", "merchant", "admin", name="userrole"),
        existing_nullable=False,
        server_default="user",
    )
    # 1b. 存量 USER 数据平滑迁移为 CUSTOMER
    op.execute("UPDATE users SET role = 'customer' WHERE role = 'user'")
    # 1c. 收缩枚举到目标集合，默认值改为 customer
    op.alter_column(
        "users",
        "role",
        existing_type=sa.Enum(
            "user", "customer", "merchant", "admin", name="userrole"
        ),
        type_=sa.Enum("customer", "merchant", "admin", name="userrole"),
        existing_nullable=False,
        server_default="customer",
    )

    # ---------- 2. products.merchant_id ----------
    op.add_column("products", sa.Column("merchant_id", sa.Integer(), nullable=True))

    # 回填前保证存在归属主体：优先用已有 MERCHANT，其次 ADMIN；
    # 若两者都不存在（历史数据全是 CUSTOMER），创建一个"系统迁移商家"
    # 承接存量商品（password_hash 为 NULL，无法登录，仅作为数据归属主体），
    # 避免 merchant_id 悬空导致 NOT NULL 约束失败。
    conn = op.get_bind()
    has_owner = conn.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE role IN ('merchant', 'admin')")
    ).scalar()
    if not has_owner:
        conn.execute(
            sa.text(
                "INSERT INTO users (name, email, password_hash, role, "
                "created_at, version) VALUES "
                "('系统迁移商家', 'migrated-merchant@system.local', NULL, "
                "'merchant', NOW(), 1)"
            )
        )

    op.execute(
        """
        UPDATE products
        SET merchant_id = COALESCE(
            (SELECT MIN(id) FROM users WHERE role = 'merchant'),
            (SELECT MIN(id) FROM users WHERE role = 'admin')
        )
        WHERE merchant_id IS NULL
        """
    )
    op.create_foreign_key(
        "fk_products_merchant_id_users",
        "products",
        "users",
        ["merchant_id"],
        ["id"],
    )
    op.create_index("ix_products_merchant_id", "products", ["merchant_id"])
    op.alter_column(
        "products", "merchant_id", existing_type=sa.Integer(), nullable=False
    )

    # ---------- 3. orders.merchant_id ----------
    op.add_column("orders", sa.Column("merchant_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN skus s ON s.id = oi.sku_id
        JOIN products p ON p.id = s.product_id
        SET o.merchant_id = p.merchant_id
        WHERE o.merchant_id IS NULL
        """
    )
    op.create_foreign_key(
        "fk_orders_merchant_id_users",
        "orders",
        "users",
        ["merchant_id"],
        ["id"],
    )
    op.create_index("ix_orders_merchant_id", "orders", ["merchant_id"])
    op.alter_column(
        "orders", "merchant_id", existing_type=sa.Integer(), nullable=False
    )


def downgrade() -> None:
    # ---------- orders.merchant_id ----------
    op.drop_index("ix_orders_merchant_id", table_name="orders")
    op.drop_constraint("fk_orders_merchant_id_users", "orders", type_="foreignkey")
    op.drop_column("orders", "merchant_id")

    # ---------- products.merchant_id ----------
    op.drop_index("ix_products_merchant_id", table_name="products")
    op.drop_constraint("fk_products_merchant_id_users", "products", type_="foreignkey")
    op.drop_column("products", "merchant_id")

    # ---------- users.role 回退为双角色 ----------
    # customer/merchant 数据无法无损映射回 user/admin，统一回退为 user
    op.execute("UPDATE users SET role = 'user' WHERE role IN ('customer', 'merchant')")
    op.alter_column(
        "users",
        "role",
        existing_type=sa.Enum("customer", "merchant", "admin", name="userrole"),
        type_=sa.Enum("user", "admin", name="userrole"),
        existing_nullable=False,
        server_default="user",
    )
