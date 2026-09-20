from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import AuthService, get_app_settings, get_auth_service
from app.core.config import Settings
from app.core.cookies import clear_refresh_cookie, set_refresh_cookie
from app.core.response import success_response
from app.schemas.auth import LoginRequest, RegisterRequest
from app.schemas.user import UserRead

router = APIRouter()


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a user",
)
async def register(
    payload: RegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    user, session = await auth_service.register(payload)
    response = success_response(
        {
            "access_token": session.access_token,
            "token_type": "bearer",
            "expires_in": session.expires_in,
            "user": UserRead.model_validate(user),
        },
        status_code=status.HTTP_201_CREATED,
    )
    set_refresh_cookie(response, session.refresh_token, settings)
    return response


@router.post("/login", summary="Log in")
async def login(
    payload: LoginRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    user, session = await auth_service.login(payload)
    response = success_response(
        {
            "access_token": session.access_token,
            "token_type": "bearer",
            "expires_in": session.expires_in,
            "user": UserRead.model_validate(user),
        }
    )
    set_refresh_cookie(response, session.refresh_token, settings)
    return response


@router.post("/refresh", summary="Rotate refresh token")
async def refresh(
    request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    user, session = await auth_service.refresh(refresh_token)
    response = success_response(
        {
            "access_token": session.access_token,
            "token_type": "bearer",
            "expires_in": session.expires_in,
            "user": UserRead.model_validate(user),
        }
    )
    set_refresh_cookie(response, session.refresh_token, settings)
    return response


@router.post("/logout", summary="Log out")
async def logout(
    request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> Response:
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    await auth_service.logout(refresh_token)
    response = success_response(None)
    clear_refresh_cookie(response, settings)
    return response


@router.get("", include_in_schema=False)
async def auth_info() -> Any:
    return success_response({"message": "Use /login, /register, /refresh or /logout."})
