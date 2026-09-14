from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.config import ALGORITHM, SECRET_KEY
from app.database.database import get_db
from app.exceptions.errors import (
    PermissionDeniedError,
    TokenExpiredError,
    TokenInvalidError,
    UserNotFoundError,
)
from app.models.user import User, UserRole

# auto_error=False：未提供 Authorization 时不自动抛 403，
# 而是返回 None，由 get_current_user 统一抛 401（TokenInvalidError）。
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise TokenInvalidError(message="未提供认证信息")

    token = credentials.credentials

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise TokenInvalidError()
        user_id = int(user_id)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError() from None
    except (InvalidTokenError, TypeError, ValueError):
        raise TokenInvalidError() from None

    user = db.get(User, user_id)
    if user is None:
        raise UserNotFoundError(message="当前用户不存在")

    return user


def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """管理员权限依赖：角色不是 ADMIN 时抛 403 PermissionDeniedError。

    用法（Router 层）：

        @router.post("/admin/products", dependencies=[Depends(require_admin)])
        # 或
        def create_product(admin: Annotated[User, Depends(require_admin)]):
            ...
    """
    if current_user.role != UserRole.ADMIN:
        raise PermissionDeniedError(message="需要管理员权限")
    return current_user
