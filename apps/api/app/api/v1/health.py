from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.response import error_response, success_response

router = APIRouter()


@router.get("/health/live", summary="Process liveness")
async def live() -> Any:
    return success_response({"status": "ok"})


@router.get("/health/ready", summary="Dependency readiness")
async def ready(request: Request) -> Any:
    settings = request.app.state.settings
    checks: dict[str, str] = {}
    ready_status = True

    try:
        async with asyncio.timeout(2):
            async with request.app.state.db_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        ready_status = False

    if settings.redis_url:
        redis = Redis.from_url(settings.redis_url)
        try:
            async with asyncio.timeout(2):
                await redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
        finally:
            await redis.aclose()
    else:
        checks["redis"] = "not_configured"

    payload = {
        "status": "ready" if ready_status else "degraded",
        "checks": checks,
    }
    if not ready_status:
        return error_response(
            status_code=503,
            code="SERVICE_NOT_READY",
            message="服务依赖尚未就绪",
            details=[{"service": name, "status": status} for name, status in checks.items()],
        )
    return success_response(payload)
