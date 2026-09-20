from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from starlette.responses import JSONResponse

from app.core.request_context import get_request_id


class ErrorDetail(BaseModel):
    field: str | None = None
    code: str
    message: str
    service: str | None = None
    status: str | None = None


class ApiError(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] = []


class ApiResponse[T](BaseModel):
    success: bool
    data: T | None = None
    meta: dict[str, Any] | None = None
    error: ApiError | None = None
    request_id: str


def success_response(
    data: Any,
    *,
    meta: dict[str, Any] | None = None,
    status_code: int = 200,
) -> JSONResponse:
    payload = ApiResponse[Any](
        success=True,
        data=data,
        meta=meta,
        error=None,
        request_id=get_request_id(),
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload),
        headers={"X-Request-ID": get_request_id()},
    )


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    payload = ApiResponse[Any](
        success=False,
        data=None,
        meta=None,
        error=ApiError(code=code, message=message, details=list(details or [])),
        request_id=get_request_id(),
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(payload),
        headers={"X-Request-ID": get_request_id()},
    )
