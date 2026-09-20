from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import Settings
from app.core.security import verify_password
from app.core.text import normalize_content
from app.db.base import Base
from app.db.seed import POEMS, seed_admin, seed_catalog
from app.db.session import create_database_engine, create_session_factory
from app.models.poem import Poem
from app.models.user import UserRole
from app.repositories.users import UserRepository
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture()
async def seed_session(api_settings: Settings) -> AsyncIterator[AsyncSession]:
    import app.models  # noqa: F401

    engine = create_database_engine(api_settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _admin_settings(api_settings: Settings, **overrides: object) -> Settings:
    values = {
        "seed_admin_email": "Admin@Example.com",
        "seed_admin_password": SecretStr("correct-horse-battery"),
        "seed_admin_display_name": "Development Admin",
    }
    values.update(overrides)
    return api_settings.model_copy(update=values)


def _canonical_poem(title: str) -> dict[str, object]:
    return next(item for item in POEMS if item["title"] == title)


async def test_seed_catalog_is_idempotent(seed_session: AsyncSession) -> None:
    first = await seed_catalog(seed_session)
    second = await seed_catalog(seed_session)

    assert first["poems"] == len(POEMS)
    assert first["repaired_poems"] == 0
    assert second["poems"] == 0
    assert second["repaired_poems"] == 0

    stored = await seed_session.scalar(select(func.count(Poem.id)))
    assert stored == len(POEMS)


async def test_seed_catalog_repairs_drifted_poem(seed_session: AsyncSession) -> None:
    await seed_catalog(seed_session)
    title = "水调歌头·明月几时有"
    poem = await seed_session.scalar(select(Poem).where(Poem.title == title))
    assert poem is not None
    version_no = poem.version_no

    poem.content = "明月几时有？把酒问青天。"
    poem.normalized_content = normalize_content(poem.content)
    await seed_session.commit()

    repaired = await seed_catalog(seed_session)

    assert repaired["poems"] == 0
    assert repaired["repaired_poems"] == 1
    refreshed = await seed_session.scalar(select(Poem).where(Poem.title == title))
    assert refreshed is not None
    assert refreshed.content == _canonical_poem(title)["content"]
    assert refreshed.version_no == version_no + 1
    stored = await seed_session.scalar(
        select(func.count(Poem.id)).where(Poem.title == title)
    )
    assert stored == 1


async def test_seed_admin_skips_without_configuration(
    seed_session: AsyncSession,
    api_settings: Settings,
) -> None:
    created = await seed_admin(seed_session, api_settings)

    assert created is False
    assert await UserRepository(seed_session).get_by_email("admin@example.com") is None


async def test_seed_admin_is_idempotent(
    seed_session: AsyncSession,
    api_settings: Settings,
) -> None:
    settings = _admin_settings(api_settings)

    assert await seed_admin(seed_session, settings) is True
    assert await seed_admin(seed_session, settings) is False

    user = await UserRepository(seed_session).get_by_email("admin@example.com")
    assert user is not None
    assert user.email == "admin@example.com"
    assert user.display_name == "Development Admin"
    assert user.role == UserRole.ADMIN.value
    assert verify_password("correct-horse-battery", user.password_hash)


async def test_seed_admin_requires_complete_configuration(
    seed_session: AsyncSession,
    api_settings: Settings,
) -> None:
    settings = _admin_settings(api_settings, seed_admin_password=None)

    with pytest.raises(RuntimeError, match="must be configured together"):
        await seed_admin(seed_session, settings)


async def test_seed_admin_is_rejected_in_production(
    seed_session: AsyncSession,
    api_settings: Settings,
) -> None:
    settings = _admin_settings(api_settings, environment="production")

    with pytest.raises(RuntimeError, match="not allowed in production"):
        await seed_admin(seed_session, settings)
