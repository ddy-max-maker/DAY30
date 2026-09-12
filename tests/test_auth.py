from app.database.redis import redis_client


def test_login_invalid_user(client):
    response = client.post(
        "/auth/login",
        json={"email": "notexist@example.com", "password": "wrongpassword"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 10001
    assert body["message"] == "账号或密码错误"
    assert body["data"] is None


def test_register_user(client):
    response = client.post(
        "/auth/register",
        json={"name": "Tom", "email": "tom@example.com", "password": "12345678"},
    )

    assert response.status_code == 200

    body = response.json()
    assert body["code"] == 0

    data = body["data"]

    assert data["name"] == "Tom"
    assert data["email"] == "tom@example.com"
    assert "id" in data

    assert "password" not in data
    assert "password_hash" not in data


def test_register_duplicate_email(client):
    user_data = {"name": "Tom", "email": "tom@example.com", "password": "12345678"}

    first_response = client.post("/auth/register", json=user_data)

    assert first_response.status_code == 200
    assert first_response.json()["code"] == 0

    second_response = client.post("/auth/register", json=user_data)

    assert second_response.status_code == 200
    body = second_response.json()
    assert body["code"] == 10002


def test_verify_code_success(client):
    email = "verify@example.com"

    request_response = client.post("/auth/code/request", json={"email": email})

    assert request_response.status_code == 200
    assert request_response.json()["code"] == 0

    code = redis_client.get(f"verify_code:{email}")

    assert code is not None

    verify_response = client.post(
        "/auth/code/verify", json={"email": email, "code": code}
    )

    assert verify_response.status_code == 200
    assert verify_response.json()["code"] == 0

    # 验证成功后验证码应该被删除
    assert redis_client.get(f"verify_code:{email}") is None


def test_verify_code_invalid(client):
    email = "verify@example.com"

    response = client.post("/auth/code/request", json={"email": email})

    assert response.status_code == 200
    assert response.json()["code"] == 0

    verify_response = client.post(
        "/auth/code/verify", json={"email": email, "code": "000000"}
    )

    assert verify_response.status_code == 200
    body = verify_response.json()
    assert body["code"] == 10000


def test_verify_code_cooldown(client):
    email = "verify@example.com"

    first_response = client.post("/auth/code/request", json={"email": email})

    assert first_response.status_code == 200
    assert first_response.json()["code"] == 0

    second_response = client.post("/auth/code/request", json={"email": email})

    assert second_response.status_code == 200
    body = second_response.json()
    assert body["code"] == 10000


def test_invalid_token(client):
    response = client.get(
        "/todos", headers={"Authorization": "Bearer this-is-not-a-valid-token"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 10001
    assert body["data"] is None
