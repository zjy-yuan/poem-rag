from __future__ import annotations

import logging
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_context import get_request_id
from app.core.response import error_response

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    AUTH_NOT_AUTHENTICATED = "AUTH_NOT_AUTHENTICATED"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
    AUTH_REFRESH_INVALID = "AUTH_REFRESH_INVALID"
    AUTH_USER_NOT_FOUND = "AUTH_USER_NOT_FOUND"
    AUTH_USER_DISABLED = "AUTH_USER_DISABLED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    USER_EMAIL_EXISTS = "USER_EMAIL_EXISTS"
    USER_PASSWORD_INVALID = "USER_PASSWORD_INVALID"
    POEM_NOT_FOUND = "POEM_NOT_FOUND"
    POEM_VERSION_CONFLICT = "POEM_VERSION_CONFLICT"
    POEM_INVALID_STATUS = "POEM_INVALID_STATUS"
    POEM_VERSION_NOT_FOUND = "POEM_VERSION_NOT_FOUND"
    CHUNKS_ALREADY_INDEXED = "CHUNKS_ALREADY_INDEXED"
    INDEX_RUN_NOT_FOUND = "INDEX_RUN_NOT_FOUND"
    INDEX_RUN_INVALID_STATUS = "INDEX_RUN_INVALID_STATUS"
    INDEX_RUN_ALREADY_ACTIVE = "INDEX_RUN_ALREADY_ACTIVE"
    EMBEDDING_PROVIDER_ERROR = "EMBEDDING_PROVIDER_ERROR"
    VECTOR_STORE_ERROR = "VECTOR_STORE_ERROR"
    AUTHOR_NOT_FOUND = "AUTHOR_NOT_FOUND"
    AUTHOR_EXISTS = "AUTHOR_EXISTS"
    DYNASTY_NOT_FOUND = "DYNASTY_NOT_FOUND"
    DYNASTY_EXISTS = "DYNASTY_EXISTS"
    DYNASTY_IN_USE = "DYNASTY_IN_USE"
    CATEGORY_NOT_FOUND = "CATEGORY_NOT_FOUND"
    CATEGORY_EXISTS = "CATEGORY_EXISTS"
    CATEGORY_HAS_CHILDREN = "CATEGORY_HAS_CHILDREN"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    CHAT_MODEL_NOT_CONFIGURED = "CHAT_MODEL_NOT_CONFIGURED"
    CHAT_EMPTY_RESPONSE = "CHAT_EMPTY_RESPONSE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_PROVIDER_ERROR = "MODEL_PROVIDER_ERROR"
    HTTP_NOT_FOUND = "HTTP_NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = list(details or [])


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        details.append(
            {
                "field": location or "body",
                "code": item.get("type", "invalid"),
                "message": item.get("msg", "Invalid value"),
            }
        )
    return details


def install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> Any:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> Any:
        return error_response(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="请求参数校验失败",
            details=_validation_details(exc),
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> Any:
        code = ErrorCode.HTTP_NOT_FOUND if exc.status_code == 404 else "HTTP_ERROR"
        return error_response(
            status_code=exc.status_code,
            code=code,
            message=str(exc.detail),
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> Any:
        logger.exception(
            "Unhandled request error",
            extra={"request_id": get_request_id(), "error_type": type(exc).__name__},
        )
        return error_response(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="服务暂时不可用",
        )
