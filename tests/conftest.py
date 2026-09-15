import os

os.environ["REDIS_DB"] = "15"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app.core.config import (
    DB_HOST,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
)
from app.core.security import create_access_token
from app.database.database import Base, get_db
from app.database.redis import redis_client
from app.main import app as fastapi_app
from tests.factories.user_factory import create_test_admin, create_test_user

# 确保所有 ORM Model 都被注册到 Base.metadata
test_database_url = URL.create(
    drivername="mysql+pymysql",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database="learner_lab_test",
)


test_engine = create_engine(test_database_url)


TestingSessionLocal = sessionmaker(bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()

    try:
        yield db

    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_test_state():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    redis_client.flushdb()

    yield

    redis_client.flushdb()


@pytest.fixture
def db_session():
    """提供测试数据库 session，供 factory 直接写库使用。

    与 override_get_db 用同一个 TestingSessionLocal，
    所以通过 db_session 写的数据对 API 请求可见。
    """
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    fastapi_app.dependency_overrides[get_db] = override_get_db

    with TestClient(fastapi_app) as test_client:
        yield test_client

    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def user_token(db_session):
    """创建一个普通用户并返回其 JWT token 字符串。

    使用 factory 直接写库，不经过注册接口，避免依赖接口行为。
    """
    user = create_test_user(db_session)
    return create_access_token(user.id)


@pytest.fixture
def admin_token(db_session):
    """创建一个管理员用户并返回其 JWT token 字符串。"""
    admin = create_test_admin(db_session)
    return create_access_token(admin.id)


@pytest.fixture
def auth_headers(client):
    """通过 HTTP 注册+登录创建用户，返回 Authorization header dict。

    保留用于测试注册/登录接口本身的场景。
    """

    def create_headers(name="Tom", email="tom@example.com", password="12345678"):
        register_response = client.post(
            "/auth/register", json={"name": name, "email": email, "password": password}
        )

        assert register_response.status_code == 200
        assert register_response.json()["code"] == 0

        login_response = client.post(
            "/auth/login", json={"email": email, "password": password}
        )

        assert login_response.status_code == 200
        assert login_response.json()["code"] == 0

        token = login_response.json()["data"]["access_token"]

        return {"Authorization": f"Bearer {token}"}

    return create_headers


@pytest.fixture
def admin_headers(client):
    """通过直接写库创建 ADMIN 用户，登录后返回 Authorization header dict。

    保留用于需要完整登录流程的场景。
    """

    def create_admin_headers(
        name="Admin", email="admin@example.com", password="12345678"
    ):
        from app.core.security import hash_password
        from app.models.user import User, UserRole

        db = TestingSessionLocal()
        admin = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        db.close()

        login_response = client.post(
            "/auth/login", json={"email": email, "password": password}
        )

        assert login_response.status_code == 200
        assert login_response.json()["code"] == 0

        token = login_response.json()["data"]["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return create_admin_headers
