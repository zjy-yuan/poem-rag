from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_catalog_service, get_retrieval_service
from app.core.response import success_response
from app.models.chunk import ChunkGranularity
from app.services.catalog import CatalogService
from app.services.retrieval import RetrievalService

router = APIRouter()


@router.get("/evidence", summary="Retrieve explainable lexical evidence")
async def retrieve_evidence(
    retrieval: Annotated[RetrievalService, Depends(get_retrieval_service)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    granularity: Annotated[list[ChunkGranularity] | None, Query()] = None,
    author_id: int | None = None,
    dynasty_id: int | None = None,
) -> Any:
    result = await retrieval.search_evidence(
        query=q,
        limit=limit,
        granularities=granularity,
        author_id=author_id,
        dynasty_id=dynasty_id,
    )
    return success_response(
        result.items,
        meta={
            "strategy": result.strategy,
            "query": q,
            "normalized_query": result.normalized_query,
            "candidate_count": result.candidate_count,
            "limit": limit,
            "granularity": [item.value for item in granularity] if granularity else None,
            "author_id": author_id,
            "dynasty_id": dynasty_id,
        },
    )


@router.get("", summary="Search published poems")
async def search(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
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
