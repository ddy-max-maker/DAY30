"""
缓存雪崩 (Cache Avalanche) 测试
================================

【什么是雪崩】
大量缓存 key 在同一时刻集中过期（或 Redis 整体宕机），
导致这一瞬间所有请求同时回源数据库，DB 压力陡增甚至被打垮。

【和击穿的区别】（对照 _test_breakdown.py）
  - 击穿：1 个热点 key 过期，大量并发抢同一份数据 → Redis 分布式锁有效
  - 雪崩：大量【不同】key 同时过期 → 锁是 per-key 的（lock:user:1、
          lock:user:2 ...），互不阻塞，10 个 key 就在同一瞬间打出
          10 次 DB 查询，锁根本拦不住

【两组对照实验】
  Part A（复现雪崩）：10 个 key 用【相同 TTL】写入 → 同时过期 → 50 个并发请求
                      预期：users 表点查集中爆发 ~10 次（每 key 1 次，锁挡住同 key 并发），
                            请求延迟因锁等待 / DB 突发负载而升高
  Part B（jitter 防护）：走正常接口预热，set_cache 的 TTL = 300 + random(0, 60)
                      预期：各 key TTL 互不相同（300~360s），过期时间被打散；
                            同样 50 并发全部命中缓存，users 表查询 = 0

【环境说明】
  后端跑在 Docker（compose.yaml）：backend 8001、redis-compose 映射到宿主机 6382、
  mysql-compose 映射到 3307。注意宿主机 6379 是其他项目的 Redis，别连错。
  DB 查询次数通过 performance_schema 按 SQL digest 统计（root 只读连接），
  比 Com_select 精确：不含连接池 pre_ping 的 SELECT 1 噪声。

用法：
  1. 启动后端：docker compose -f compose.yaml -f compose.dev.yaml up -d
  2. 运行脚本：.venv\\Scripts\\python.exe _test_avalanche.py
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import redis as redis_lib
import requests
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8001"
TARGET_KEYS = 10            # 模拟 10 个不同的缓存 key
CONCURRENCY_PER_KEY = 5     # 每个 key 5 个并发 → 共 50 并发
SAME_TTL = 2                # Part A：相同短 TTL，制造“同时过期”


def make_redis_client():
    """探测服务端实际使用的 Redis。

    Docker 部署时 redis-compose 映射在宿主机 6382；
    裸跑时回退到项目配置端口（.env REDIS_PORT）。
    """

    candidates = []
    for port in (6382, settings.REDIS_PORT):
        if port not in candidates:
            candidates.append(port)

    for port in candidates:
        try:
            r = redis_lib.Redis(
                host="127.0.0.1",
                port=port,
                db=0,
                decode_responses=True,
                protocol=2,
                socket_connect_timeout=2,
            )
            r.ping()
            return r, port
        except Exception:
            continue

    raise RuntimeError("找不到可用的 Redis（6382 / 配置端口均不可达）")


_env = dotenv_values(".env")

# 业务账号无权访问 performance_schema，用 root 只读连接做 DB 查询计数
root_engine = create_engine(
    URL.create(
        drivername="mysql+pymysql",
        username="root",
        password=_env.get("MYSQL_ROOT_PASSWORD", "root"),
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
    )
)


def users_select_count() -> int:
    """users 表主键点查（db.get(User, id)）累计执行次数。

    来自 performance_schema 按 SQL digest 聚合，窗口前后取差值。
    """

    with root_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT IFNULL(SUM(COUNT_STAR), 0)
                FROM performance_schema.events_statements_summary_by_digest
                WHERE SCHEMA_NAME = DATABASE()
                  AND DIGEST_TEXT LIKE 'SELECT `users`%'
                  AND DIGEST_TEXT LIKE '%`users_id`%'
                """
            )
        ).fetchone()
        return int(row[0])


def is_user_cached(rds: redis_lib.Redis, uid: int) -> bool:
    """user:{uid} 是否为【真实用户缓存】。

    注意：统一异常处理器会把 500 包装成 HTTP 200（code=50000, data=null），
    例如 email 脏数据（'123string.com' 缺 @）会让 UserResponse 校验失败、
    缓存永远无法回填。因此不能只看 HTTP 状态码，必须验证缓存真的建立了。
    """

    cached = rds.get(f"user:{uid}")
    if not cached:
        return False
    try:
        value = json.loads(cached)
    except json.JSONDecodeError:
        return False
    return isinstance(value, dict) and "email" in value


def consider(rds: redis_lib.Redis, uid: int) -> bool:
    """请求一次 /users/{uid}，返回该用户是否为“可正常缓存”的健康用户"""

    r = requests.get(f"{BASE_URL}/users/{uid}", timeout=5)
    if r.status_code != 200:
        return False
    if r.json().get("data") is None:
        return False  # 业务错误包装（50000/10003 等），data 为 null
    return is_user_cached(rds, uid)


def prepare_user_ids(rds: redis_lib.Redis) -> list[int]:
    """收集 TARGET_KEYS 个“可正常缓存”的 user id（不足则自动注册）"""

    ids = []

    for uid in range(1, 51):
        if consider(rds, uid):
            ids.append(uid)
        if len(ids) >= TARGET_KEYS:
            break

    if len(ids) < TARGET_KEYS:
        need = TARGET_KEYS - len(ids)
        print(f"  健康用户不足 {TARGET_KEYS} 个，自动注册 {need} 个测试用户...")
        ts = int(time.time())
        for n in range(need):
            r = requests.post(
                f"{BASE_URL}/auth/register",
                json={
                    "name": f"avalanche_test_{ts}_{n}",
                    "email": f"avalanche_{ts}_{n}@example.com",
                    "password": "Test12345",
                },
                timeout=5,
            )
            if r.status_code == 200 and r.json().get("data"):
                new_id = r.json()["data"]["id"]
                if consider(rds, new_id):
                    ids.append(new_id)
                else:
                    # 注册后立即查询存在可见性竞态，可能误写空值缓存（__NULL__）；
                    # 清掉空值/锁缓存，稍等重试一次
                    rds.delete(f"user:{new_id}", f"lock:user:{new_id}")
                    time.sleep(1)
                    if consider(rds, new_id):
                        ids.append(new_id)

    return ids[:TARGET_KEYS]


def clear_caches(rds: redis_lib.Redis, ids: list[int]) -> None:
    """删除缓存 key 和可能残留的分布式锁 key"""

    keys = []
    for uid in ids:
        keys += [f"user:{uid}", f"lock:user:{uid}"]
    rds.delete(*keys)


def burst(ids: list[int]):
    """对每个 key 发 CONCURRENCY_PER_KEY 个并发 GET

    返回 (ok, fail, 总耗时, 升序延迟列表)
    """

    urls = [
        f"{BASE_URL}/users/{uid}"
        for uid in ids
        for _ in range(CONCURRENCY_PER_KEY)
    ]

    def one(url: str):
        t0 = time.perf_counter()
        r = requests.get(url, timeout=15)
        return r.status_code, time.perf_counter() - t0

    start = time.perf_counter()
    latencies = []
    ok = fail = 0

    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = [pool.submit(one, u) for u in urls]
        for f in as_completed(futures):
            code, lat = f.result()
            latencies.append(lat)
            if code == 200:
                ok += 1
            else:
                fail += 1

    return ok, fail, time.perf_counter() - start, sorted(latencies)


def report(name: str, ok: int, fail: int, elapsed: float,
           lats: list[float], db_delta: int) -> None:
    print(f"\n  [{name}] 结果:")
    print(f"    HTTP 200: {ok}   失败: {fail}")
    print(f"    并发总耗时: {elapsed:.2f}s")
    print(
        f"    延迟 最快/中位/最慢: "
        f"{lats[0] * 1000:.0f}ms / "
        f"{lats[len(lats) // 2] * 1000:.0f}ms / "
        f"{lats[-1] * 1000:.0f}ms"
    )
    print(f"    users 表点查次数（本窗口）: {db_delta} 次")


def main():
    print("=" * 64)
    print("前置检查")
    print("=" * 64)

    try:
        rds, redis_port = make_redis_client()
        rds.ping()
        print(f"✅ Redis 连接正常（宿主机端口 {redis_port}）")
    except Exception as e:
        print(f"❌ Redis 连接失败: {e}")
        return

    print("\n准备测试数据：收集 10 个可正常缓存的健康用户 ...")
    ids = prepare_user_ids(rds)
    if len(ids) < 2:
        print("❌ 可用用户不足，无法演示雪崩")
        return
    print(f"✅ 测试用户 id: {ids}")

    total = len(ids) * CONCURRENCY_PER_KEY

    # ================= Part A：复现雪崩 =================
    print("\n" + "=" * 64)
    print("Part A：复现雪崩 —— 10 个 key 相同 TTL，同时过期")
    print("=" * 64)

    clear_caches(rds, ids)

    # 模拟“同一批预热、TTL 完全相同”的缓存
    for uid in ids:
        rds.set(
            f"user:{uid}",
            json.dumps({"id": uid}),
            ex=SAME_TTL,
        )
    print(f"  已写入 {len(ids)} 个 key，TTL 全部 = {SAME_TTL}s")

    print(f"  等待 {SAME_TTL + 0.6}s，让它们【同一时刻】集体过期 ...")
    time.sleep(SAME_TTL + 0.6)

    alive = [uid for uid in ids if rds.get(f"user:{uid}") is not None]
    if alive:
        print(f"  ⚠️ 仍有 key 未过期: {alive}")
    else:
        print("  ✅ 所有 key 已同时失效（雪崩条件就绪）")

    print(f"  瞬间爆发 {total} 个并发请求 ...")
    db_before = users_select_count()
    ok_a, fail_a, elapsed_a, lats_a = burst(ids)
    db_delta_a = users_select_count() - db_before
    report("Part A 雪崩", ok_a, fail_a, elapsed_a, lats_a, db_delta_a)
    print(
        f"  → 解读：{len(ids)} 个 key 同时 miss，DB 同一瞬间被打了 "
        f"{db_delta_a} 次点查；每 key 内部的 5 个并发被分布式锁挡住"
    )

    # ================= Part B：jitter 防护 =================
    print("\n" + "=" * 64)
    print("Part B：jitter 防护 —— 正常预热，TTL 随机打散")
    print("=" * 64)

    clear_caches(rds, ids)
    print("  串行请求 /users/{id} 预热（走 set_cache：TTL=300+random(0,60)）...")
    for uid in ids:
        requests.get(f"{BASE_URL}/users/{uid}", timeout=5)

    print("\n  各 key 当前 TTL（互不相同 → 过期时间被打散）:")
    ttls = []
    for uid in ids:
        ttl = rds.ttl(f"user:{uid}")
        ttls.append(ttl)
        print(f"    user:{uid:<4} TTL = {ttl}s")
    print(f"  TTL 分布: {min(ttls)}s ~ {max(ttls)}s，最大相差 {max(ttls) - min(ttls)}s")

    print(f"\n  缓存全部存活，同样爆发 {total} 个并发请求 ...")
    db_before = users_select_count()
    ok_b, fail_b, elapsed_b, lats_b = burst(ids)
    db_delta_b = users_select_count() - db_before
    report("Part B 防护", ok_b, fail_b, elapsed_b, lats_b, db_delta_b)
    print("  → 解读：请求全部命中 Redis，users 表点查 = 0，DB 毫无压力")

    # ================= 对比与结论 =================
    print("\n" + "=" * 64)
    print("📊 两组对比")
    print("=" * 64)
    print(f"  {'指标':<22}{'Part A 雪崩':>14}{'Part B jitter':>16}")
    print(f"  {'-' * 52}")
    print(f"  {'users 表点查次数':<20}{db_delta_a:>14}{db_delta_b:>16}")
    print(f"  {'并发总耗时(s)':<21}{elapsed_a:>14.2f}{elapsed_b:>16.2f}")
    print(f"  {'最慢请求(ms)':<21}{lats_a[-1] * 1000:>14.0f}{lats_b[-1] * 1000:>16.0f}")

    print("\n" + "=" * 64)
    print("📌 结论")
    print("=" * 64)
    print("""
  1. 雪崩成因：批量预热的数据使用了【相同 TTL】，到期瞬间集体失效，
     所有请求同时回源 DB（Part A 中 ~10 次点查集中在同一瞬间）。
  2. 分布式锁防不住雪崩：锁是 per-key 的（lock:user:1 / lock:user:2 ...），
     它只保证“同一个 key”只回源一次（防击穿）；10 个 key 同时 miss 时，
     10 把锁互不阻塞，DB 同一瞬间承受 10 次突发查询。
  3. 本项目的防护：cache_service.set_cache(expire=300, jitter=60)
     在基础 TTL 上叠加 random(0, 60) 秒随机值（见 Part B 的 TTL 分布），
     把集中过期打散成零散过期，DB 压力被摊平。
  4. 另一类雪崩——Redis 整体宕机：靠 jitter 防不住，需要
     Redis 哨兵/集群高可用 + 服务熔断降级/限流 + 本地多级缓存兜底。
""")


if __name__ == "__main__":
    main()
