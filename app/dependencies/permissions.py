"""统一角色权限依赖（RBAC）。

角色校验集中在这一处，Router 层通过 Depends 使用，
不在每个接口里散落 if user.role == ... 判断。

注意：这里只做"角色判断"；资源归属判断（如商品属于哪个商家）
在 service 层完成，两者缺一不可。
"""

from typing import Annotated, Callable

from fastapi import Depends

from app.dependencies.auth import get_current_user
from app.exceptions.errors import PermissionDeniedError
from app.models.user import User, UserRole


def require_roles(*allowed_roles: UserRole) -> Callable[..., User]:
    """生成"允许指定角色集合"的权限依赖工厂。

    用法（一般直接用下面的 require_customer 等现成依赖）：

        Depends(require_roles(UserRole.ADMIN, UserRole.MERCHANT))
    """

    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in allowed_roles:
            allowed = ", ".join(role.value for role in allowed_roles)
            raise PermissionDeniedError(message=f"需要以下角色之一：{allowed}")
        return current_user

    return dependency


require_customer = require_roles(UserRole.CUSTOMER)
require_merchant = require_roles(UserRole.MERCHANT)
require_admin = require_roles(UserRole.ADMIN)
