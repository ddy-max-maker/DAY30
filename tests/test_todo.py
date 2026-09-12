def test_create_and_get_todo(client, auth_headers):
    headers = auth_headers()

    create_response = client.post(
        "/todos", json={"title": "Learn pytest", "completed": False}, headers=headers
    )

    assert create_response.status_code == 200
    assert create_response.json()["code"] == 0

    created_todo = create_response.json()["data"]

    assert created_todo["title"] == "Learn pytest"
    assert created_todo["completed"] is False
    assert "id" in created_todo

    get_response = client.get("/todos", headers=headers)

    assert get_response.status_code == 200

    body = get_response.json()
    assert body["code"] == 0

    data = body["data"]

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["title"] == "Learn pytest"


def test_create_todo_without_token(client):
    response = client.post(
        "/todos", json={"title": "Unauthorized Todo", "completed": False}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 10001
    assert body["data"] is None


def test_update_own_todo(client, auth_headers):
    headers = auth_headers()

    create_response = client.post(
        "/todos", json={"title": "Old title", "completed": False}, headers=headers
    )

    assert create_response.status_code == 200
    assert create_response.json()["code"] == 0

    todo_id = create_response.json()["data"]["id"]

    update_response = client.patch(
        f"/todos/{todo_id}",
        json={"title": "New title", "completed": True},
        headers=headers,
    )

    assert update_response.status_code == 200

    body = update_response.json()
    assert body["code"] == 0

    data = body["data"]

    assert data["title"] == "New title"
    assert data["completed"] is True


def test_user_cannot_update_other_users_todo(client, auth_headers):
    alice_headers = auth_headers(name="Alice", email="alice@example.com")

    create_response = client.post(
        "/todos",
        json={"title": "Alice Todo", "completed": False},
        headers=alice_headers,
    )

    assert create_response.status_code == 200
    assert create_response.json()["code"] == 0

    todo_id = create_response.json()["data"]["id"]

    bob_headers = auth_headers(name="Bob", email="bob@example.com")

    response = client.patch(
        f"/todos/{todo_id}",
        json={"title": "Bob tries to change it"},
        headers=bob_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 10001


def test_delete_own_todo(client, auth_headers):
    headers = auth_headers()

    create_response = client.post(
        "/todos", json={"title": "Delete me", "completed": False}, headers=headers
    )

    assert create_response.status_code == 200
    assert create_response.json()["code"] == 0

    todo_id = create_response.json()["data"]["id"]

    delete_response = client.delete(f"/todos/{todo_id}", headers=headers)

    assert delete_response.status_code == 200
    assert delete_response.json()["code"] == 0

    get_response = client.get("/todos", headers=headers)

    assert get_response.status_code == 200

    body = get_response.json()
    assert body["code"] == 0

    data = body["data"]
    assert data["total"] == 0
    assert data["items"] == []


def test_get_nonexistent_todo(client, auth_headers):
    headers = auth_headers()

    response = client.get("/todos/999999", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 10003


def test_user_cannot_delete_other_users_todo(client, auth_headers):
    alice_headers = auth_headers(name="Alice", email="alice@example.com")

    create_response = client.post(
        "/todos",
        json={"title": "Alice Todo", "completed": False},
        headers=alice_headers,
    )

    assert create_response.status_code == 200
    assert create_response.json()["code"] == 0

    todo_id = create_response.json()["data"]["id"]

    bob_headers = auth_headers(name="Bob", email="bob@example.com")

    response = client.delete(f"/todos/{todo_id}", headers=bob_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 10001
