from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import get_catalog_service, require_admin
from app.core.response import success_response
from app.models.user import User
from app.schemas.catalog import DynastyCreate, DynastyUpdate
from app.services.catalog import CatalogService

router = APIRouter()


@router.get("", summary="List dynasties for administration")
async def list_dynasties(
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.list_dynasties())


@router.post("", status_code=201, summary="Create a dynasty")
async def create_dynasty(
    payload: DynastyCreate,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.create_dynasty(payload), status_code=201)


@router.patch("/{dynasty_id}", summary="Update a dynasty")
async def update_dynasty(
    dynasty_id: int,
    payload: DynastyUpdate,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    return success_response(await catalog.update_dynasty(dynasty_id, payload))


@router.delete("/{dynasty_id}", summary="Delete an unused dynasty")
async def delete_dynasty(
    dynasty_id: int,
    _: Annotated[User, Depends(require_admin)],
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> Any:
    await catalog.delete_dynasty(dynasty_id)
    return success_response(None)
