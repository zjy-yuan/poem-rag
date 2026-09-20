from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_catalog_service
from app.core.response import success_response
from app.services.catalog import CatalogService

router = APIRouter()


@router.get("", summary="List authors")
async def list_authors(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=100)] = None,
    dynasty_id: int | None = None,
) -> Any:
    result = await catalog.list_authors(
        page=page,
        page_size=page_size,
        q=q,
        dynasty_id=dynasty_id,
    )
    return success_response(result.items, meta=result.meta.model_dump())


@router.get("/{author_id}", summary="Get an author and published works")
async def get_author(
    author_id: int,
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.get_author(author_id))
