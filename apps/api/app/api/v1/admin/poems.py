from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_admin_catalog_service, get_admin_domain_label_service
from app.core.response import success_response
from app.models.domain_label import (
    DomainLabelGenerationMethod,
    DomainLabelReviewStatus,
)
from app.models.poem import PoemStatus
from app.schemas.catalog import PoemCreate, PoemUpdate
from app.schemas.domain_label import DomainLabelAssignmentCreate
from app.services.catalog import CatalogService
from app.services.domain_labels import DomainLabelService

router = APIRouter()


@router.get("", summary="List poems for administration")
async def list_poems(
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[PoemStatus | None, Query()] = None,
    include_deleted: bool = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
    author_id: int | None = None,
    dynasty_id: int | None = None,
    category_id: int | None = None,
) -> Any:
    result = await catalog.list_admin_poems(
        page=page,
        page_size=page_size,
        status=status.value if status is not None else None,
        include_deleted=include_deleted,
        q=q,
        author_id=author_id,
        dynasty_id=dynasty_id,
        category_id=category_id,
    )
    return success_response(result.items, meta=result.meta.model_dump())


@router.post("", status_code=201, summary="Create a draft poem")
async def create_poem(
    payload: PoemCreate,
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
) -> Any:
    return success_response(await catalog.create_poem(payload), status_code=201)


@router.get("/{poem_id}", summary="Get a poem for administration")
async def get_poem(
    poem_id: int,
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
) -> Any:
    return success_response(await catalog.get_admin_poem(poem_id))


@router.patch("/{poem_id}", summary="Update a poem")
async def update_poem(
    poem_id: int,
    payload: PoemUpdate,
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
) -> Any:
    return success_response(await catalog.update_poem(poem_id, payload))


@router.delete("/{poem_id}", summary="Soft delete a poem")
async def delete_poem(
    poem_id: int,
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
) -> Any:
    await catalog.delete_poem(poem_id)
    return success_response(None)


@router.post("/{poem_id}/publish", summary="Publish a poem")
async def publish_poem(
    poem_id: int,
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
) -> Any:
    return success_response(await catalog.publish_poem(poem_id))


@router.post("/{poem_id}/unpublish", summary="Unpublish a poem")
async def unpublish_poem(
    poem_id: int,
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
) -> Any:
    return success_response(await catalog.unpublish_poem(poem_id))


@router.post("/{poem_id}/restore", summary="Restore a soft-deleted poem")
async def restore_poem(
    poem_id: int,
    catalog: Annotated[CatalogService, Depends(get_admin_catalog_service)],
) -> Any:
    return success_response(await catalog.restore_poem(poem_id))


@router.post(
    "/{poem_id}/domain-labels",
    summary="Create a domain label assignment for the current poem version",
)
async def create_poem_domain_label(
    poem_id: int,
    payload: DomainLabelAssignmentCreate,
    domain_labels: Annotated[
        DomainLabelService,
        Depends(get_admin_domain_label_service),
    ],
) -> Any:
    assignment, created = await domain_labels.create_assignment(poem_id, payload)
    return success_response(assignment, status_code=201 if created else 200)


@router.get(
    "/{poem_id}/domain-labels",
    summary="List domain label assignments for the current poem version",
)
async def list_poem_domain_labels(
    poem_id: int,
    domain_labels: Annotated[
        DomainLabelService,
        Depends(get_admin_domain_label_service),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    review_status: Annotated[DomainLabelReviewStatus | None, Query()] = None,
    generation_method: Annotated[
        DomainLabelGenerationMethod | None,
        Query(),
    ] = None,
) -> Any:
    result = await domain_labels.list_poem_assignments(
        poem_id=poem_id,
        page=page,
        page_size=page_size,
        review_status=review_status.value if review_status is not None else None,
        generation_method=(
            generation_method.value if generation_method is not None else None
        ),
    )
    return success_response(result.items, meta=result.meta.model_dump())
