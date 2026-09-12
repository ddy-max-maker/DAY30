"""Demonstrate storing a short-lived verification code in Redis."""

import secrets

from app.database.redis import redis_client

CODE_TTL_SECONDS = 60


def main() -> None:
    email = "test@example.com"
    code = str(secrets.randbelow(900_000) + 100_000)
    cache_key = f"verify_code:{email}"

    redis_client.set(cache_key, code, ex=CODE_TTL_SECONDS)
    print("验证码：", code)
    print("Redis 中的验证码：", redis_client.get(cache_key))
    print("剩余时间：", redis_client.ttl(cache_key))

    saved_code = redis_client.get(cache_key)
    input_code = input("请输入验证码：")

    if saved_code is None:
        print("验证码不存在或已过期")
    elif input_code != saved_code:
        print("验证码错误")
    else:
        print("验证码正确")
        redis_client.delete(cache_key)


if __name__ == "__main__":
    main()
