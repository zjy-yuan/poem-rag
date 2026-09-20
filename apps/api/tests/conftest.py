from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr


@pytest.fixture()
def api_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        debug=True,
        database_url="sqlite+aiosqlite://",
        auto_create_tables=True,
        redis_url=None,
        jwt_secret=SecretStr("test-secret-with-enough-entropy-0001"),
    )


@pytest.fixture()
def client(api_settings: Settings) -> Iterator[TestClient]:
    app = create_app(api_settings)
    with TestClient(app) as test_client:
        yield test_client
