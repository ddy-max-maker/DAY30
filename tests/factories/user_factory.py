"""用户测试工厂。

只负责创建 User 测试对象，不包含业务断言。
"""

from app.core.security import hash_password
from app.models.user import User, UserRole


def create_test_user(
    db,
    name: str = "TestUser",
    email: str = "test_user@example.com",
    password: str = "12345678",
) -> User:
    """创建一个普通用户（role=USER）。"""
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.USER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_admin(
    db,
    name: str = "TestAdmin",
    email: str = "test_admin@example.com",
    password: str = "12345678",
) -> User:
    """创建一个管理员用户（role=ADMIN）。

    注册接口只能创建 USER，ADMIN 必须直接写库（模拟内部脚本）。
    """
    admin = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin
