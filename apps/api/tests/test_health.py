from __future__ import annotations

from app import __version__
from fastapi.testclient import TestClient


def test_application_version_matches_package_version(client: TestClient) -> None:
    assert getattr(client.app, "version", None) == __version__


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
