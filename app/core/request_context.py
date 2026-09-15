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
