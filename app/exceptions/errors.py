"""
统一业务错误定义
错误码规范：
  0      -> 成功
  10000  -> 通用业务错误
  10001  -> 认证/鉴权错误（账号密码、JWT、权限）
  10002  -> 用户相关错误（不存在、邮箱重复）
  10003  -> Todo 相关错误
  422    -> 参数校验错误（由 RequestValidationError handler 处理）
"""


class BusinessError(Exception):
    """统一业务异常基类。Service / Router 层都应该抛这个，
    由 handler 统一转换成 {code, message, data}。

    status_code: HTTP 状态码，默认 200（业务码语义）；
    需要真实 HTTP 错误码的异常（如乐观锁 409）可覆盖。
    """

    def __init__(
        self,
        message: str = "业务异常",
        code: int = 10000,
        status_code: int = 200,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------- 认证相关 ----------
class AuthError(BusinessError):
    def __init__(self, message: str = "账号或密码错误"):
        super().__init__(message=message, code=10001)


class TokenInvalidError(BusinessError):
    def __init__(self, message: str = "无效的 Token"):
        super().__init__(message=message, code=10001)


class TokenExpiredError(BusinessError):
    def __init__(self, message: str = "Token 已过期"):
        super().__init__(message=message, code=10001)


class PermissionDeniedError(BusinessError):
    def __init__(self, message: str = "无权限访问"):
        super().__init__(message=message, code=10001)


# ---------- 用户相关 ----------
class UserNotFoundError(BusinessError):
    def __init__(self, message: str = "用户不存在"):
        super().__init__(message=message, code=10002)


class EmailAlreadyExistsError(BusinessError):
    def __init__(self, message: str = "邮箱已存在"):
        super().__init__(message=message, code=10002)


class VersionConflictError(BusinessError):
    """乐观锁冲突：客户端携带的 version 已过期（别人抢先修改了）。

    HTTP 409 Conflict，客户端应重新 GET 最新数据后重试。
    """

    def __init__(self, message: str = "数据已被其他请求修改，请刷新后重试"):
        super().__init__(message=message, code=10005, status_code=409)


# ---------- Todo 相关 ----------
class TodoNotFoundError(BusinessError):
    def __init__(self, message: str = "Todo 不存在"):
        super().__init__(message=message, code=10003)
