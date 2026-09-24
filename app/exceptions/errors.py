"""
统一业务错误定义
错误码规范：
  0      -> 成功
  10000  -> 通用业务错误
  10001  -> 认证/鉴权错误（账号密码、JWT、权限）
  10002  -> 用户相关错误（不存在、邮箱重复）
  422    -> 参数校验错误（由 RequestValidationError handler 处理）
  50000  -> 服务器内部错误（未捕获异常）

HTTP status code 语义：
  400 Bad Request          —— 客户端请求参数/语义错误（如空订单）
  401 Unauthorized          —— 未认证 / 认证失败
  403 Forbidden             —— 已认证但无权限
  404 Not Found             —— 资源不存在
  409 Conflict              —— 资源/状态冲突（邮箱重复、库存不足等）
  422 Unprocessable Entity  —— 参数校验失败（Pydantic 层）
  500 Internal Server Error —— 服务端未捕获异常

HTTP status 反映协议层结果，body.code 是业务层细分码，两者独立。
"""


class BusinessError(Exception):
    """统一业务异常基类。Service / Router 层都应该抛这个，
    由 handler 统一转换成 {code, message, data}。

    status_code: HTTP 状态码，默认 400（Bad Request），
    子类应按语义显式覆盖（401/403/404/409 等）。
    """

    def __init__(
        self,
        message: str = "业务异常",
        code: int = 10000,
        status_code: int = 400,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code


# ---------- 认证相关 ----------
class AuthError(BusinessError):
    """账号或密码错误：HTTP 401 Unauthorized。"""

    def __init__(self, message: str = "账号或密码错误"):
        super().__init__(message=message, code=10001, status_code=401)


class TokenInvalidError(BusinessError):
    """Token 无效或未提供：HTTP 401 Unauthorized。"""

    def __init__(self, message: str = "无效的 Token"):
        super().__init__(message=message, code=10001, status_code=401)


class TokenExpiredError(BusinessError):
    """Token 已过期：HTTP 401 Unauthorized。"""

    def __init__(self, message: str = "Token 已过期"):
        super().__init__(message=message, code=10001, status_code=401)


class PermissionDeniedError(BusinessError):
    """权限不足：已登录但角色不满足要求（如 USER 访问 ADMIN 接口），
    或尝试操作不属于自己的资源。

    HTTP 403 Forbidden。
    """

    def __init__(self, message: str = "无权限访问"):
        super().__init__(message=message, code=10001, status_code=403)


# ---------- 用户相关 ----------
class UserNotFoundError(BusinessError):
    """用户不存在：HTTP 404 Not Found。"""

    def __init__(self, message: str = "用户不存在"):
        super().__init__(message=message, code=10002, status_code=404)


class EmailAlreadyExistsError(BusinessError):
    """邮箱已存在（注册冲突）：HTTP 409 Conflict。"""

    def __init__(self, message: str = "邮箱已存在"):
        super().__init__(message=message, code=10002, status_code=409)


class VersionConflictError(BusinessError):
    """乐观锁冲突：客户端携带的 version 已过期（别人抢先修改了）。

    HTTP 409 Conflict，客户端应重新 GET 最新数据后重试。
    """

    def __init__(self, message: str = "数据已被其他请求修改，请刷新后重试"):
        super().__init__(message=message, code=10005, status_code=409)


# ---------- 电商相关 ----------
class ProductNotFoundError(BusinessError):
    """商品不存在：HTTP 404 Not Found。"""

    def __init__(self, message: str = "商品不存在"):
        super().__init__(message=message, code=10100, status_code=404)


class SKUNotFoundError(BusinessError):
    """SKU 不存在：HTTP 404 Not Found。"""

    def __init__(self, message: str = "SKU 不存在"):
        super().__init__(message=message, code=10101, status_code=404)


class SKUNotAvailableError(BusinessError):
    """SKU 不可销售（已下架或商品已下架）。

    下单时遇到=请求与当前资源状态冲突：HTTP 409 Conflict。
    """

    def __init__(self, message: str = "商品已下架，无法购买"):
        super().__init__(message=message, code=10102, status_code=409)


class InsufficientStockError(BusinessError):
    """库存不足，下单失败。

    请求与可用库存冲突：HTTP 409 Conflict。
    """

    def __init__(self, message: str = "库存不足"):
        super().__init__(message=message, code=10103, status_code=409)


class OrderNotFoundError(BusinessError):
    """订单不存在：HTTP 404 Not Found。"""

    def __init__(self, message: str = "订单不存在"):
        super().__init__(message=message, code=10104, status_code=404)


class OrderStatusError(BusinessError):
    """订单状态不允许当前操作（如已取消的订单不能再取消）。

    请求与订单当前状态冲突：HTTP 409 Conflict。
    """

    def __init__(self, message: str = "订单状态不允许此操作"):
        super().__init__(message=message, code=10105, status_code=409)


class OrderNotEmptyError(BusinessError):
    """下单时订单项为空：HTTP 400 Bad Request。"""

    def __init__(self, message: str = "订单不能为空"):
        super().__init__(message=message, code=10106, status_code=400)


class CrossMerchantOrderError(BusinessError):
    """跨商家下单：当前版本一个订单只能购买同一商家的商品。

    请求内容与订单归属规则冲突（不拆单）：HTTP 409 Conflict。
    """

    def __init__(self, message: str = "当前版本一个订单只能购买同一商家的商品"):
        super().__init__(message=message, code=10107, status_code=409)


class IdempotencyConflictError(BusinessError):
    """幂等键冲突：同一个 Idempotency-Key 已被用于不同内容的请求。

    HTTP 409 Conflict，客户端必须更换新的 Idempotency-Key 重试。
    """

    def __init__(self, message: str = "Idempotency-Key 已用于不同的请求"):
        super().__init__(message=message, code=10108, status_code=409)


class PaymentConflictError(BusinessError):
    """支付冲突：订单已支付但收到不同的支付流水号，
    或支付流水号已被其他订单占用。

    HTTP 409 Conflict。
    """

    def __init__(self, message: str = "支付信息冲突"):
        super().__init__(message=message, code=10109, status_code=409)


class PaymentAuthError(BusinessError):
    """支付回调身份校验失败：X-Mock-Payment-Secret 缺失或不匹配。

    模拟支付网关用共享密钥模拟支付平台身份校验；
    真实生产环境应替换为支付平台的数字签名验签机制。

    HTTP 401 Unauthorized（调用者身份未认证）。
    """

    def __init__(self, message: str = "支付回调密钥无效"):
        super().__init__(message=message, code=10110, status_code=401)


# ---------- 购物车相关 ----------
class CartError(BusinessError):
    """购物车操作失败（Redis 异常等）：HTTP 500 Internal Server Error。

    Redis 不可用属于服务端故障，不应向客户端暴露底层细节。
    """

    def __init__(self, message: str = "购物车服务异常"):
        super().__init__(message=message, code=10200, status_code=500)
