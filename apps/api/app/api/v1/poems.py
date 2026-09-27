from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_catalog_service, get_domain_label_service
from app.core.response import success_response
from app.services.catalog import CatalogService
from app.services.domain_labels import DomainLabelService

router = APIRouter()


@router.get("", summary="List published poems")
async def list_poems(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=100)] = None,
    author_id: int | None = None,
    dynasty_id: int | None = None,
    category_id: int | None = None,
) -> Any:
    result = await catalog.list_poems(
        page=page,
        page_size=page_size,
        q=q,
        author_id=author_id,
        dynasty_id=dynasty_id,
        category_id=category_id,
    )
    return success_response(result.items, meta=result.meta.model_dump())


@router.get("/{poem_id}", summary="Get a published poem")
async def get_poem(
    poem_id: int,
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.get_poem(poem_id))


@router.get(
    "/{poem_id}/domain-labels",
    summary="List approved domain labels for a published poem",
)
async def list_poem_domain_labels(
    poem_id: int,
    domain_labels: Annotated[DomainLabelService, Depends(get_domain_label_service)],
) -> Any:
    return success_response(await domain_labels.list_public_poem_labels(poem_id))
