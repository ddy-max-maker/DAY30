import json
import random

from app.database.redis import redis_client

NULL_CACHE = "__NULL__"


def get_cache(key: str):
    """从 Redis 获取缓存，JSON 反序列化，不存在返回 None"""

    value = redis_client.get(key)

    if value:
        return json.loads(value)

    return None


def set_cache(
    key: str,
    value,
    expire: int = 300,
    jitter: int = 60,
):
    """
    写入缓存。

    expire: 基础 TTL
    jitter: 随机增加的 TTL 范围
    """

    actual_expire = expire

    if jitter > 0:
        actual_expire += random.randint(0, jitter)

    redis_client.set(
        key,
        json.dumps(value, default=str),
        ex=actual_expire,
    )


def delete_cache(key: str):
    """删除指定缓存"""

    redis_client.delete(key)
