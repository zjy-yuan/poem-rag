from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_catalog_service
from app.core.response import success_response
from app.schemas.catalog import CategoryType
from app.services.catalog import CatalogService

router = APIRouter()


@router.get("", summary="List active categories")
async def list_categories(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    type: Annotated[CategoryType | None, Query()] = None,
) -> Any:
    categories = await catalog.list_categories(
        category_type=type.value if type is not None else None,
        include_inactive=False,
        public_only=True,
    )
    return success_response(categories)
