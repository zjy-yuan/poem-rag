from __future__ import annotations

from fastapi import Response

from app.core.config import Settings


def set_refresh_cookie(
    response: Response,
    refresh_token: str,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=f"{settings.api_v1_prefix}/auth",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path=f"{settings.api_v1_prefix}/auth",
    )
