"""可观测性测试：Request ID 链路、请求日志字段、敏感信息过滤。

覆盖：
- response 含 X-Request-ID
- 不同请求生成不同 request_id
- 422/401/404 仍返回 request_id
- 请求日志含 method / path / status_code / duration
- 日志不含 Authorization token
- 客户端传入非法 X-Request-ID 自动重置
- 500 异常 ERROR 日志保留 traceback
"""

import logging

import pytest

from app.main import app

# ============ 1. response header 测试 ============


def test_response_contains_x_request_id(client):
    """1. 正常请求 response 含 X-Request-ID。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Request-ID" in response.headers


def test_x_request_id_non_empty(client):
    """2. X-Request-ID 非空。"""
    response = client.get("/health")
    rid = response.headers["X-Request-ID"]
    assert rid
    assert rid != "-"
    assert len(rid) > 0


def test_two_requests_have_different_request_ids(client):
    """3. 两个独立请求生成不同 request_id。"""
    r1 = client.get("/health")
    r2 = client.get("/health")
    assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]


def test_422_response_still_has_request_id(client):
    """5. 422 请求仍返回 request_id。"""
    # POST /auth/register 缺字段触发参数校验失败
    response = client.post("/auth/register", json={})
    assert response.status_code == 422
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"]


def test_401_response_still_has_request_id(client):
    """6. 401 请求仍返回 request_id。

    不带 token 访问需认证接口 → TokenInvalidError → HTTP 401。
    """
    response = client.get("/users/me")
    assert response.status_code == 401
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"]


def test_403_response_still_has_request_id(client, auth_headers):
    """额外. 403 请求仍返回 request_id。

    普通用户访问 admin 接口 → PermissionDeniedError → HTTP 403。
    """
    user_headers = auth_headers()
    # 普通用户尝试创建商品（admin 接口）
    response = client.post(
        "/admin/products",
        json={"name": "Phone", "description": "test"},
        headers=user_headers,
    )
    assert response.status_code == 403
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"]


def test_404_response_still_has_request_id(client):
    """7. 404 请求仍返回 request_id。

    业务 404：访问不存在的用户 → UserNotFoundError → HTTP 404。
    """
    response = client.get("/users/999999")
    assert response.status_code == 404
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"]


def test_client_supplied_valid_request_id_is_used(client):
    """额外：客户端传入合规的 X-Request-ID 被复用。"""
    custom_rid = "my-request-id-12345"
    response = client.get("/health", headers={"X-Request-ID": custom_rid})
    assert response.headers["X-Request-ID"] == custom_rid


def test_client_supplied_invalid_request_id_is_rejected(client):
    """额外：客户端传入非法 X-Request-ID（含换行符）被拒绝，服务端生成新的。"""
    # 含换行符 → 日志注入风险，应被替换
    malicious_rid = "abc\nfake-log-line"
    response = client.get("/health", headers={"X-Request-ID": malicious_rid})
    rid = response.headers["X-Request-ID"]
    assert rid != malicious_rid
    assert "\n" not in rid


def test_client_supplied_too_long_request_id_is_rejected(client):
    """额外：客户端传入超长 X-Request-ID（>128）被拒绝，服务端生成新的。"""
    long_rid = "a" * 200
    response = client.get("/health", headers={"X-Request-ID": long_rid})
    rid = response.headers["X-Request-ID"]
    assert rid != long_rid
    assert len(rid) <= 128


# ============ 2. 日志内容测试（caplog）============


@pytest.fixture
def caplog_backend(caplog):
    """让 caplog 能捕获 `backend` logger 的输出。

    caplog 默认捕获 root logger；我们 backend logger propagate=False，
    需要显式 set_level 让 caplog 的 handler 接收 backend 的日志。
    """
    caplog.set_level(logging.INFO, logger="backend")
    return caplog


def test_200_log_contains_request_id_and_fields(client, caplog_backend):
    """4 + 8. 200 请求日志含 request_id、method、path、status_code、duration。"""
    response = client.get("/health")
    rid = response.headers["X-Request-ID"]

    # 找到 request_completed 这条日志
    completed_logs = [
        r for r in caplog_backend.records if "request_completed" in r.message
    ]
    assert len(completed_logs) >= 1
    record = completed_logs[-1]

    # request_id 必须能从 record 上拿到（由 RequestIdFilter 注入）
    assert record.request_id == rid

    # message 含所有必要字段
    msg = record.message
    assert "method=GET" in msg
    assert "path=/health" in msg
    assert "status_code=200" in msg
    assert "duration_ms=" in msg


def test_log_does_not_contain_authorization_token(client, auth_headers, caplog_backend):
    """9. 日志中不会出现 Authorization token。"""
    headers = auth_headers()
    # 触发一个需要 token 的请求
    response = client.get("/users/me", headers=headers)
    assert response.status_code == 200

    # 检查所有日志，任何一行都不能含 Bearer / token 字符串
    for record in caplog_backend.records:
        msg_lower = record.message.lower()
        assert "bearer" not in msg_lower
        # JWT header 通常以 eyJ 开头，日志里不应出现
        assert "ey" not in msg_lower or "bearer" in msg_lower


# ============ 3. 异常日志测试 ============


def test_business_exception_logs_info_level(client, caplog_backend):
    """业务异常（如 401）记 INFO/WARNING，无 ERROR traceback。"""
    # 不带 token 访问需认证接口 → 401
    response = client.get("/users/me")
    assert response.status_code == 401

    # 不应有 ERROR 级别的 unhandled_exception 记录
    unhandled_errors = [
        r
        for r in caplog_backend.records
        if r.levelno >= logging.ERROR and "unhandled_exception" in r.message
    ]
    assert len(unhandled_errors) == 0


def test_unhandled_exception_logs_error_with_traceback(caplog_backend):
    """10. 未处理异常 → ERROR 级别 + traceback + HTTP 500。

    通过临时给 app 加一个会抛 Exception 的路由来触发，
    测试结束清理（不引入生产 debug API）。

    使用 raise_server_exceptions=False 的 TestClient，
    让兜底 exception_handler 拦截异常并返回 HTTP 500 + code=50000，
    而不是把异常 re-raise 给 pytest。
    """
    from fastapi.testclient import TestClient

    from app.database.database import get_db
    from tests.conftest import override_get_db

    caplog_backend.set_level(logging.INFO, logger="backend")

    def _raise_unexpected():
        raise RuntimeError("boom for test")

    # 临时加一个会抛未捕获异常的路由
    app.add_api_route(
        "/_test_500", _raise_unexpected, methods=["GET"], name="_test_500"
    )

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            response = test_client.get("/_test_500")

        # 兜底 handler 返回 HTTP 500 + code=50000
        assert response.status_code == 500
        assert response.json()["code"] == 50000
        assert "X-Request-ID" in response.headers

        # 必须有 ERROR 级别日志
        error_records = [
            r
            for r in caplog_backend.records
            if r.levelno >= logging.ERROR and "unhandled_exception" in r.message
        ]
        assert len(error_records) >= 1

        # 必须保留 traceback（logger.exception → exc_info=True）
        record = error_records[-1]
        assert record.exc_info is not None
        # exc_info 是 (type, value, tb) 三元组
        assert record.exc_info[0] is RuntimeError
        assert str(record.exc_info[1]) == "boom for test"
    finally:
        # 清理临时路由：从 app.routes 移除
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "name", None) != "_test_500"
        ]
        app.dependency_overrides.clear()
