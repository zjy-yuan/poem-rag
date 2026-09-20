from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import get_auth_service, get_current_user
from app.core.response import success_response
from app.models.user import User
from app.schemas.user import PasswordChangeRequest, UserRead, UserUpdateRequest
from app.services.auth import AuthService

router = APIRouter()


@router.get("/me", summary="Get current user")
async def me(user: Annotated[User, Depends(get_current_user)]) -> Any:
    return success_response(UserRead.model_validate(user))


@router.patch("/me", summary="Update current user")
async def update_me(
    payload: UserUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> Any:
    updated = await auth_service.update_profile(user, payload)
    return success_response(UserRead.model_validate(updated))


@router.post("/me/password", summary="Change password")
async def change_password(
    payload: PasswordChangeRequest,
    user: Annotated[User, Depends(get_current_user)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> Any:
    await auth_service.change_password(user, payload)
    return success_response(None)
