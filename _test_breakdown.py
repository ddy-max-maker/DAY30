"""
缓存击穿测试：
  1. 手动清除 user:1 的 Redis 缓存（模拟热点 key 过期）
  2. 瞬间发起 20 个并发 GET /users/1
  3. 观察：有分布式锁时，DB QUERY 应该只出现 1 次
           无分布式锁时，DB QUERY 会出现 20 次

用法：先启动 uvicorn，再运行这个脚本。
     同时观察 uvicorn 的控制台日志（搜 [DB QUERY]）。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from app.database.redis import redis_client

URL = "http://127.0.0.1:8001/users/1"
CONCURRENT = 20


def main():
    # ---------- 前置：确认 Redis 可用 ----------
    print("=" * 60)
    print("前置检查")
    print("=" * 60)
    try:
        redis_client.ping()
        print("✅ Redis 连接正常")
    except Exception as e:
        print(f"❌ Redis 连接失败: {e}")
        return

    # ---------- Step 1: 预热一次，确保 DB 里有 user:1 ----------
    print("\n" + "=" * 60)
    print("Step 1: 预热 —— 确保 DB 有 user:1")
    print("=" * 60)
    r = requests.get(URL, timeout=5)
    print(f"  状态码: {r.status_code}")
    if r.status_code != 200:
        print("  ❌ /users/1 查不到，先确保数据库里有 id=1 的用户")
        return

    # ---------- Step 2: 验证缓存已建立 ----------
    print("\n" + "=" * 60)
    print("Step 2: 验证缓存已建立")
    print("=" * 60)
    cached = redis_client.get("user:1")
    if cached:
        print(f"✅ Redis user:1 已缓存 ({len(cached)} bytes)")
    else:
        print("❌ 缓存没建立，检查一下 Cache Service")
        return

    # ---------- Step 3: 手动删缓存 —— 制造"热点 key 过期"场景 ----------
    print("\n" + "=" * 60)
    print("Step 3: 手动 DEL user:1 —— 模拟热点 key 过期")
    print("=" * 60)
    deleted = redis_client.delete("user:1")
    print(f"  DEL user:1 返回: {deleted} (1=成功)")
    assert redis_client.get("user:1") is None, "缓存没删掉？"
    print("✅ 缓存已清空，击穿条件就绪")

    # ---------- Step 4: 瞬间爆发 20 个并发请求 ----------
    print("\n" + "=" * 60)
    print(f"Step 4: 瞬间爆发 {CONCURRENT} 个并发 GET /users/1")
    print("=" * 60)

    def one_request(i):
        r = requests.get(URL, timeout=5)
        return i, r.status_code

    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENT) as pool:
        futures = [pool.submit(one_request, i) for i in range(CONCURRENT)]
        for f in as_completed(futures):
            results.append(f.result())

    ok = sum(1 for _, code in results if code == 200)
    fail = sum(1 for _, code in results if code != 200)
    print(f"\n  ✅ 200 OK: {ok}    ❌ 失败: {fail}")

    # ---------- Step 5: 验证缓存只被回填了一次 ----------
    print("\n" + "=" * 60)
    print("Step 5: 验证缓存最终状态")
    print("=" * 60)
    cached = redis_client.get("user:1")
    if cached:
        ttl = redis_client.ttl("user:1")
        print(f"✅ user:1 已回填，TTL={ttl}s (应该是 300 左右)")
    else:
        print("❌ user:1 没回填？")

    # ---------- 结论提示 ----------
    print("\n" + "=" * 60)
    print("📌 判定方法：看 uvicorn 控制台日志里 '[DB QUERY]' 出现了几次")
    print("=" * 60)
    print("""
  ✅ 分布式锁生效  → [DB QUERY] 只出现 1 次（持锁线程查 DB，其他线程等锁后 Double Check 命中缓存）
  ❌ 没有防护      → [DB QUERY] 出现 20 次（全部并发都打到 DB）
""")


if __name__ == "__main__":
    main()