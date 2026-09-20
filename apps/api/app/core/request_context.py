from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_valid_request_id = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def get_request_id() -> str:
    return _request_id.get() or "-"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _valid_request_id.fullmatch(incoming) else f"req_{uuid4().hex}"
        token = _request_id.set(request_id)
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            _request_id.reset(token)
