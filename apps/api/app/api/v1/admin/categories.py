from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_catalog_service, require_admin
from app.core.response import success_response
from app.models.user import User
from app.schemas.catalog import CategoryCreate, CategoryType, CategoryUpdate
from app.services.catalog import CatalogService

router = APIRouter()


@router.get("", summary="List categories for administration")
async def list_categories(
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    type: Annotated[CategoryType | None, Query()] = None,
) -> Any:
    categories = await catalog.list_categories(
        category_type=type.value if type is not None else None,
        include_inactive=True,
        public_only=False,
    )
    return success_response(categories)


@router.post("", status_code=201, summary="Create a category")
async def create_category(
    payload: CategoryCreate,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.create_category(payload), status_code=201)


@router.patch("/{category_id}", summary="Update a category")
async def update_category(
    category_id: int,
    payload: CategoryUpdate,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.update_category(category_id, payload))


@router.delete("/{category_id}", summary="Disable a category")
async def disable_category(
    category_id: int,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    await catalog.disable_category(category_id)
    return success_response(None)
