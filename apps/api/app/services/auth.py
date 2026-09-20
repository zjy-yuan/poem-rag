from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.security import (
    create_access_token,
    ensure_utc,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.user import User, UserRole, UserStatus
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest
from app.schemas.user import PasswordChangeRequest, UserUpdateRequest


@dataclass(frozen=True)
class AuthSession:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def register(self, payload: RegisterRequest) -> tuple[User, AuthSession]:
        email = payload.email.lower()
        if await self.users.get_by_email(email):
            raise AppError(
                status_code=409,
                code=ErrorCode.USER_EMAIL_EXISTS,
                message="该邮箱已注册",
            )

        user = await self.users.create(
            email=email,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            role=UserRole.USER.value,
        )
        session = await self._issue_session(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user, session

    async def login(self, payload: LoginRequest) -> tuple[User, AuthSession]:
        user = await self.users.get_by_email(payload.email.lower())
        if user is None or not verify_password(payload.password, user.password_hash):
            raise AppError(
                status_code=401,
                code=ErrorCode.AUTH_INVALID_CREDENTIALS,
                message="邮箱或密码错误",
            )
        if user.status != UserStatus.ACTIVE.value:
            raise AppError(
                status_code=403,
                code=ErrorCode.AUTH_USER_DISABLED,
                message="用户已被禁用",
            )

        session = await self._issue_session(user)
        await self.session.commit()
        return user, session

    async def refresh(self, raw_token: str | None) -> tuple[User, AuthSession]:
        if not raw_token:
            raise AppError(
                status_code=401,
                code=ErrorCode.AUTH_REFRESH_INVALID,
                message="登录状态已失效，请重新登录",
            )

        stored = await self.refresh_tokens.get_active(hash_refresh_token(raw_token))
        if stored is None or ensure_utc(stored.expires_at) <= datetime.now(UTC):
            raise AppError(
                status_code=401,
                code=ErrorCode.AUTH_REFRESH_INVALID,
                message="登录状态已失效，请重新登录",
            )
        if stored.user.status != UserStatus.ACTIVE.value:
            raise AppError(
                status_code=403,
                code=ErrorCode.AUTH_USER_DISABLED,
                message="用户已被禁用",
            )

        await self.refresh_tokens.revoke(stored, datetime.now(UTC))
        session = await self._issue_session(stored.user)
        await self.session.commit()
        return stored.user, session

    async def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        stored = await self.refresh_tokens.get_active(hash_refresh_token(raw_token))
        if stored is not None:
            await self.refresh_tokens.revoke(stored, datetime.now(UTC))
            await self.session.commit()

    async def update_profile(
        self,
        user: User,
        payload: UserUpdateRequest,
    ) -> User:
        user.display_name = payload.display_name
        await self.session.commit()
        return user

    async def change_password(
        self,
        user: User,
        payload: PasswordChangeRequest,
    ) -> None:
        if not verify_password(payload.current_password, user.password_hash):
            raise AppError(
                status_code=400,
                code=ErrorCode.USER_PASSWORD_INVALID,
                message="当前密码不正确",
            )
        user.password_hash = hash_password(payload.new_password)
        await self.session.commit()

    async def _issue_session(self, user: User) -> AuthSession:
        access_token, expires_in = create_access_token(
            user_id=user.id,
            role=user.role,
            settings=self.settings,
        )
        refresh_token = generate_refresh_token()
        await self.refresh_tokens.create(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=self.settings.refresh_token_expire_days),
        )
        return AuthSession(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )
