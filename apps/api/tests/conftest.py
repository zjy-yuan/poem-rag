from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr

CONVERTED_CORPUS_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "import"
    / "generated"
    / "chinese-gushiwen-1000-v2.json"
)


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


@pytest.fixture()
def converted_corpus_records() -> list[dict[str, object]]:
    if not CONVERTED_CORPUS_PATH.is_file():
        pytest.skip(
            "generated third-party corpus is not committed; run "
            "apps/api/scripts/convert_chinese_gushiwen.py --limit 1000 "
            "to enable corpus reference checks"
        )

    payload = json.loads(CONVERTED_CORPUS_PATH.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise AssertionError("converted corpus must contain a records list")
    return records
