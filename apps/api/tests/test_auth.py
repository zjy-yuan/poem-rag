from __future__ import annotations

from fastapi.testclient import TestClient


def register(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "reader@example.com",
            "password": "correct-horse-battery",
            "display_name": "读者",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_register_login_refresh_and_logout(client: TestClient) -> None:
    body = register(client)
    assert body["success"] is True
    access_token = body["data"]["access_token"]
    assert body["data"]["user"]["email"] == "reader@example.com"
    assert body["data"]["user"]["created_at"]
    assert body["data"]["user"]["updated_at"]
    assert body["request_id"].startswith("req_")

    me = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me.status_code == 200
    assert me.json()["data"]["display_name"] == "读者"

    refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["access_token"] != access_token

    logged_out = client.post("/api/v1/auth/logout")
    assert logged_out.status_code == 200

    refresh_after_logout = client.post("/api/v1/auth/refresh")
    assert refresh_after_logout.status_code == 401
    assert refresh_after_logout.json()["error"]["code"] == "AUTH_REFRESH_INVALID"


def test_duplicate_email_uses_stable_error_code(client: TestClient) -> None:
    register(client)
    duplicate = client.post(
        "/api/v1/auth/register",
        json={
            "email": "reader@example.com",
            "password": "correct-horse-battery",
            "display_name": "另一个读者",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USER_EMAIL_EXISTS"


def test_invalid_credentials_are_not_leaked(client: TestClient) -> None:
    register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "reader@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_validation_error_uses_envelope(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "short", "display_name": ""},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]
