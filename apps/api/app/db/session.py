from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    url = settings.database_url or ""
    engine_options: dict[str, object] = {
        "echo": settings.sql_echo,
        "pool_pre_ping": not url.startswith("sqlite"),
    }
    if url.startswith("sqlite+aiosqlite"):
        engine_options.update(
            {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        )
    return create_async_engine(url, **engine_options)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
