"""缓存击穿精确测试：用 Redis 计数器统计 DB 实际被查询的次数"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from app.database.redis import redis_client

URL = "http://127.0.0.1:8001/users/1"
N = 20

# 1. 清零计数器 + 删缓存
redis_client.delete("breakdown:db:count")
redis_client.delete("user:1")
print("[OK] step1: DB计数器清零, user:1 缓存已删")

# 2. 预热一次（确保 user:1 在 DB 里存在）
r = requests.get(URL, timeout=5)
print(f"[OK] step2: 预热请求 status={r.status_code}")
print(f"     预热后 DB 计数器 = {redis_client.get('breakdown:db:count')}")

# 3. 再次清缓存 + 清零计数器（关键！制造纯净的击穿场景）
redis_client.delete("breakdown:db:count")
redis_client.delete("user:1")
time.sleep(0.1)
print(f"\n[BOOM] 击穿场景就绪: DB count={redis_client.get('breakdown:db:count')}, cache={redis_client.get('user:1')}")

# 4. 瞬间爆发并发
def one(_):
    return requests.get(URL, timeout=5).status_code

with ThreadPoolExecutor(max_workers=N) as pool:
    futures = [pool.submit(one, i) for i in range(N)]
    codes = [f.result() for f in as_completed(futures)]

ok = sum(1 for c in codes if c == 200)

# 5. 读计数器
db_count = int(redis_client.get("breakdown:db:count") or 0)
final_cache = bool(redis_client.get("user:1"))

print(f"\n========== RESULT ==========")
print(f"  concurrent   = {N}")
print(f"  200 OK       = {ok}")
print(f"  DB queries   = {db_count}   (should be 1)")
print(f"  cache back   = {final_cache}")
print(f"============================")
print()

if db_count == 1:
    print(">>> PERFECT! Lock + Double Check works! 20 threads hit DB only 1 time.")
elif db_count < N:
    print(f">>> PARTIAL. DB hit {db_count} times (better than {N}, but expected 1)")
else:
    print(f">>> FAILED. No protection! DB hit {db_count} times (expected 1)")