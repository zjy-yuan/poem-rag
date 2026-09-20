from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import get_catalog_service
from app.core.response import success_response
from app.services.catalog import CatalogService

router = APIRouter()


@router.get("", summary="List dynasties")
async def list_dynasties(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.list_dynasties())
