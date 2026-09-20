from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.request_context import RequestIdMiddleware
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    engine = create_database_engine(app_settings)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if app_settings.auto_create_tables:
            # Import models before creating metadata for local and test environments.
            import app.models  # noqa: F401

            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        yield
        await engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        debug=app_settings.debug,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.db_engine = engine
    application.state.session_factory = session_factory

    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    install_exception_handlers(application)
    application.include_router(api_router, prefix=app_settings.api_v1_prefix)
    return application


app = create_app()
