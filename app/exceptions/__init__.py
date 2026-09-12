"""统一业务异常包。

Service / Router 层可直接：

    from app.exceptions import BusinessError, UserNotFoundError
"""

from app.exceptions.errors import (
    AuthError,
    BusinessError,
    EmailAlreadyExistsError,
    PermissionDeniedError,
    TodoNotFoundError,
    TokenExpiredError,
    TokenInvalidError,
    UserNotFoundError,
    VersionConflictError,
)

__all__ = [
    "AuthError",
    "BusinessError",
    "EmailAlreadyExistsError",
    "PermissionDeniedError",
    "TodoNotFoundError",
    "TokenExpiredError",
    "TokenInvalidError",
    "UserNotFoundError",
    "VersionConflictError",
]
