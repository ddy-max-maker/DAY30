"""
乐观锁（OCC）最终测试
======================

验证 update_user() 的完整链路：

    GET 拿 version
        ↓
    PUT + version（条件更新 WHERE version=?）
        ↓
    rowcount=1 → COMMIT → 删 Redis → 返回 version+1
    rowcount=0 → 409 Conflict（数据已被其他请求修改）

测试项：
  1. 顺序更新：携带当前 version → 成功，version+1
  2. 过期冲突：携带旧 version → HTTP 409，数据不被覆盖
  3. 缓存一致性：更新成功后 Redis 缓存被删除；下次 GET 重建新缓存
  4. 并发抢写：10 个客户端拿同一 version 同时 PUT → 恰好 1 个成功，9 个 409

用法：后端运行中（docker compose -f compose.yaml -f compose.dev.yaml up -d）
      .venv\\Scripts\\python.exe _test_optimistic_lock.py
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import redis as redis_lib
import requests

from app.core.config import settings


BASE_URL = "http://127.0.0.1:8001"
USER_ID = 1


def make_redis_client() -> redis_lib.Redis:
    """探测服务端实际使用的 Redis（docker 映射 6382，裸跑回退配置端口）"""

    for port in dict.fromkeys([6382, settings.REDIS_PORT]):
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
            print(f"✅ Redis 连接正常（宿主机端口 {port}）")
            return r
        except Exception:
            continue
    raise RuntimeError("找不到可用的 Redis")


def get_user() -> dict:
    r = requests.get(f"{BASE_URL}/users/{USER_ID}", timeout=10)
    body = r.json()
    assert body.get("data"), f"GET /users/{USER_ID} 失败: {body}"
    return body["data"]


def put_user(payload: dict):
    return requests.put(
        f"{BASE_URL}/users/{USER_ID}",
        json=payload,
        timeout=15,
    )


def check(label: str, cond: bool, detail: str = "") -> None:
    mark = "✅" if cond else "❌"
    print(f"  {mark} {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        raise AssertionError(f"测试失败: {label} {detail}")


def main():
    print("=" * 64)
    print("前置检查")
    print("=" * 64)
    rds = make_redis_client()
    cache_key = f"user:{USER_ID}"

    u0 = get_user()
    print(f"✅ 目标用户: id={u0['id']} name={u0['name']!r} version={u0['version']}")

    stamp = time.strftime("%H%M%S")

    # =====================================================
    print("\n" + "=" * 64)
    print("测试 1：顺序更新（携带当前 version → 成功）")
    print("=" * 64)
    v0 = u0["version"]
    r = put_user({"name": f"ol_A_{stamp}", "version": v0})
    body = r.json()
    print(f"  PUT name=ol_A_{stamp}, version={v0} -> HTTP {r.status_code}, body={body}")
    check("HTTP 200 + code 0", r.status_code == 200 and body["code"] == 0)
    check(f"version {v0} -> {v0 + 1}", body["data"]["version"] == v0 + 1,
          f"实际 version={body['data']['version']}")

    # =====================================================
    print("\n" + "=" * 64)
    print("测试 2：过期版本冲突（携带旧 version → 409）")
    print("=" * 64)
    r = put_user({"name": f"ol_B_stale_{stamp}", "version": v0})
    body = r.json()
    print(f"  PUT name=ol_B_stale_{stamp}, version={v0}(已过期) -> HTTP {r.status_code}, body={body}")
    check("HTTP 409 Conflict", r.status_code == 409, f"实际 HTTP {r.status_code}")
    check("业务码 10005", body["code"] == 10005, f"实际 code={body['code']}")
    check("冲突提示语", "已被其他请求修改" in body["message"], body["message"])

    u1 = get_user()
    check("数据未被覆盖（name 保持 A 的修改）",
          u1["name"] == f"ol_A_{stamp}", f"实际 name={u1['name']!r}")
    check("version 未变", u1["version"] == v0 + 1, f"实际 version={u1['version']}")

    # =====================================================
    print("\n" + "=" * 64)
    print("测试 3：缓存一致性（更新成功 → Redis 缓存被删除）")
    print("=" * 64)
    v1 = u1["version"]
    r = put_user({"name": f"ol_C_{stamp}", "version": v1})
    body = r.json()
    print(f"  PUT name=ol_C_{stamp}, version={v1} -> HTTP {r.status_code}, code={body['code']}")
    check("更新成功", r.status_code == 200 and body["code"] == 0)

    cached = rds.get(cache_key)
    check("更新后缓存已被删除（Cache Aside：先库后缓存）",
          cached is None, f"user:{USER_ID} = {cached!r}"[:80])

    u2 = get_user()
    check("GET 回源拿到新数据", u2["name"] == f"ol_C_{stamp}" and u2["version"] == v1 + 1)

    cached = rds.get(cache_key)
    check("缓存已用新数据重建", cached is not None and f"ol_C_{stamp}" in cached
          and f'"version": {v1 + 1}' in cached,
          (cached or "")[:70])

    # =====================================================
    print("\n" + "=" * 64)
    print("测试 4：并发抢写（10 个客户端拿同一 version 同时 PUT）")
    print("=" * 64)
    u_now = get_user()
    vc = u_now["version"]
    n = 10
    print(f"  当前 version={vc}，{n} 个客户端同时携带 version={vc} 抢写 ...")

    def race(i: int):
        resp = put_user({"name": f"ol_race_{i}_{stamp}", "version": vc})
        return i, resp.status_code, resp.json()

    results = []
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(race, i) for i in range(n)]
        for f in as_completed(futures):
            results.append(f.result())

    winners = [(i, body) for i, code, body in results if code == 200 and body["code"] == 0]
    losers = [(i, body) for i, code, body in results if code == 409]
    others = [(i, code, body) for i, code, body in results if code != 200 and code != 409]

    print(f"  成功: {len(winners)} 个  冲突409: {len(losers)} 个  其他: {len(others)} 个")
    check(f"恰好 1 个成功（乐观锁只放行一个赢家）", len(winners) == 1,
          f"实际 {len(winners)} 个")
    check(f"其余 {n - 1} 个全部 409", len(losers) == n - 1 and not others,
          f"409={len(losers)}, other={len(others)}")

    final = get_user()
    winner_name = f"ol_race_{winners[0][0]}_{stamp}"
    check(f"最终 name = 赢家({winner_name}) 的修改", final["name"] == winner_name,
          f"实际 name={final['name']!r}")
    check(f"最终 version = {vc + 1}（只 +1 一次）", final["version"] == vc + 1,
          f"实际 version={final['version']}")

    # =====================================================
    print("\n" + "=" * 64)
    print("📊 全部通过！乐观锁链路总结")
    print("=" * 64)
    print(f"""
  GET /users/{USER_ID}  →  version={vc}
      ↓
  并发 PUT（同一 version）→ 1 成功 / {n - 1} 个 409
      ↓
  UPDATE users SET name=?, version=version+1
  WHERE id={USER_ID} AND version={vc}
      ↓ rowcount=1
  COMMIT → DEL user:{USER_ID} → 返回 version={vc + 1}
      ↓ rowcount=0
  rollback → 409 Conflict（数据已被其他请求修改，请刷新后重试）
""")


if __name__ == "__main__":
    main()
