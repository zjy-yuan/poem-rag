from __future__ import annotations

from fastapi.testclient import TestClient


def test_liveness_response_contract(client: TestClient) -> None:
    response = client.get(
        "/api/v1/health/live",
        headers={"X-Request-ID": "test-request-123"},
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-123"
    assert response.json() == {
        "success": True,
        "data": {"status": "ok"},
        "meta": None,
        "error": None,
        "request_id": "test-request-123",
    }
