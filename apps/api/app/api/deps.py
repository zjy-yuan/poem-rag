from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.providers.chat import ChatModelPort
from app.ai.providers.deepseek import create_deepseek_chat_provider
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.security import decode_access_token
from app.models.user import User, UserRole, UserStatus
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.catalog import CatalogService
from app.services.chat import ChatService
from app.services.retrieval import RetrievalService

bearer_scheme = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthService:
    return AuthService(session, settings)


def get_catalog_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CatalogService:
    return CatalogService(session)


def get_retrieval_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RetrievalService:
    return RetrievalService(session)


def get_chat_provider(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ChatModelPort | None:
    return create_deepseek_chat_provider(settings)


def get_chat_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ChatService:
    return ChatService(session, settings)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            status_code=401,
            code=ErrorCode.AUTH_NOT_AUTHENTICATED,
            message="请先登录",
        )

    claims = decode_access_token(credentials.credentials, settings)
    user_id = int(claims["sub"])
    user = await UserRepository(session).get_by_id(user_id)

    if user is None:
        raise AppError(
            status_code=401,
            code=ErrorCode.AUTH_USER_NOT_FOUND,
            message="用户不存在或已失效",
        )
    if user.status != UserStatus.ACTIVE.value:
        raise AppError(
            status_code=403,
            code=ErrorCode.AUTH_USER_DISABLED,
            message="用户已被禁用",
        )
    return user


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if user.role != UserRole.ADMIN.value:
        raise AppError(
            status_code=403,
            code=ErrorCode.AUTH_FORBIDDEN,
            message="没有执行此操作的权限",
        )
    return user


async def get_admin_catalog_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> CatalogService:
    return CatalogService(session, changed_by_id=admin.id)
