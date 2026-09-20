from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_catalog_service, require_admin
from app.core.response import success_response
from app.models.user import User
from app.schemas.catalog import AuthorCreate, AuthorUpdate
from app.services.catalog import CatalogService

router = APIRouter()


@router.get("", summary="List authors for administration")
async def list_authors(
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=100)] = None,
    dynasty_id: int | None = None,
) -> Any:
    result = await catalog.list_admin_authors(
        page=page,
        page_size=page_size,
        q=q,
        dynasty_id=dynasty_id,
    )
    return success_response(result.items, meta=result.meta.model_dump())


@router.post("", status_code=201, summary="Create an author")
async def create_author(
    payload: AuthorCreate,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.create_author(payload), status_code=201)


@router.get("/{author_id}", summary="Get an author for administration")
async def get_author(
    author_id: int,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.get_admin_author(author_id))


@router.patch("/{author_id}", summary="Update an author")
async def update_author(
    author_id: int,
    payload: AuthorUpdate,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.update_author(author_id, payload))


@router.delete("/{author_id}", summary="Soft delete an author")
async def delete_author(
    author_id: int,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    await catalog.delete_author(author_id)
    return success_response(None)
