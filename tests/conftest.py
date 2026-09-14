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
from app.database.database import Base, get_db
from app.database.redis import redis_client
from app.main import app as fastapi_app

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
def client():

    fastapi_app.dependency_overrides[get_db] = override_get_db

    with TestClient(fastapi_app) as test_client:
        yield test_client

    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client):

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
    """创建 ADMIN 用户并返回认证头。

    注册接口只能创建 USER，ADMIN 通过直接写库创建（模拟内部脚本）。
    """

    def create_admin_headers(name="Admin", email="admin@example.com", password="12345678"):
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
