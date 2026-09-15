"""请求上下文：基于 ContextVar 的 request_id 存取。

设计要点：
1. ContextVar 是协程安全的，每个请求独立，不会串号。
2. `request_id_context.set(...)` 由 middleware 在请求入口设置，
   由 middleware 在请求出口（finally）清理，避免上下文泄漏。
3. 支持客户端传入 X-Request-ID，但必须通过 `sanitize_request_id` 安检：
   - 长度 ≤ 128（防超长字符串塞爆日志）
   - 字符集白名单 [A-Za-z0-9_\-]（防日志注入：换行/控制字符）
   - 不合规自动 fallback 为新生成的 uuid4
4. 请求外（启动期）调用 `.get()` 返回默认值 "-"，
   logger 的 RequestIdFilter 也会兜底成 "-"。
"""

import re
import uuid
from contextvars import ContextVar

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")

# 客户端传入的 X-Request-ID 安全字符集：字母、数字、下划线、连字符
_SAFE_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def generate_request_id() -> str:
    """生成新的 request_id（uuid4 字符串）。"""
    return str(uuid.uuid4())


def sanitize_request_id(raw: str | None) -> str:
    """对客户端传入的 X-Request-ID 做安检。

    规则：
    - 长度 1-128
    - 只允许 [A-Za-z0-9_\-]
    - 不合规则生成新的 uuid4

    这样防止日志注入（攻击者塞换行符伪造日志行）和超长字符串。
    """
    if raw and _SAFE_REQUEST_ID_PATTERN.match(raw):
        return raw
    return generate_request_id()
