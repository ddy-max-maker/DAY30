import time

import redis
import requests
from sqlalchemy import text

from app.database.database import engine

BASE = "http://127.0.0.1:8001"
rds = redis.Redis(host="127.0.0.1", port=6382, db=0, decode_responses=True, protocol=2)

ts = int(time.time())
email = f"probe2_{ts}@example.com"

r = requests.post(
    f"{BASE}/auth/register",
    json={"name": "probe2", "email": email, "password": "Test12345"},
    timeout=10,
)
body = r.json()
print("register body:", body)
new_id = body["data"]["id"]

time.sleep(1)

# 宿主机直连 MySQL(3307) 查
with engine.connect() as conn:
    row = conn.execute(text("SELECT id, name, email FROM users WHERE id = :i"), {"i": new_id}).fetchone()
    print("direct DB query:", row)

# 再走 API 查（先删 NULL 缓存）
rds.delete(f"user:{new_id}")
g = requests.get(f"{BASE}/users/{new_id}", timeout=10)
print("API after del NULL cache:", g.json().get("data"))
print("cache now:", (rds.get(f"user:{new_id}") or "")[:80])
