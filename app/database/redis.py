import redis

from app.core.config import (
    REDIS_DB,
    REDIS_HOST,
    REDIS_PORT,
)

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
    protocol=2,
)
