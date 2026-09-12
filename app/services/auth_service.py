import secrets
from typing import Literal

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.database.redis import redis_client
from app.models.user import User
from app.services import user_service

CreateCodeResult = Literal["created", "rate_limited", "cooldown"]
VerifyCodeResult = Literal["success", "expired", "invalid"]

CODE_TTL_SECONDS = 60
RATE_LIMIT_SECONDS = 600
MAX_CODE_REQUESTS = 5


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = user_service.get_user_by_email(db, email)

    if user is None or user.password_hash is None:
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user


def create_verify_code(email: str) -> CreateCodeResult:
    if not _is_code_request_allowed(email):
        return "rate_limited"

    cooldown_key = f"verify_code:cooldown:{email}"
    created = redis_client.set(
        cooldown_key,
        "1",
        ex=CODE_TTL_SECONDS,
        nx=True,
    )

    if not created:
        return "cooldown"

    code = str(secrets.randbelow(900_000) + 100_000)
    redis_client.set(
        f"verify_code:{email}",
        code,
        ex=CODE_TTL_SECONDS,
    )
    return "created"


def verify_code(email: str, code: str) -> VerifyCodeResult:
    cache_key = f"verify_code:{email}"
    saved_code = redis_client.get(cache_key)

    if saved_code is None:
        return "expired"

    if saved_code != code:
        return "invalid"

    redis_client.delete(cache_key)
    return "success"


def _is_code_request_allowed(email: str) -> bool:
    rate_key = f"verify_code:rate:{email}"
    count = redis_client.incr(rate_key)

    if count == 1:
        redis_client.expire(rate_key, RATE_LIMIT_SECONDS)

    return count <= MAX_CODE_REQUESTS
