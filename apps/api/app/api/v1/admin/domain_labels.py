from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_admin_domain_label_service
from app.core.response import success_response
from app.models.domain_label import (
    DomainLabelDimension,
    DomainLabelGenerationMethod,
    DomainLabelReviewStatus,
    DomainLabelStatus,
)
from app.schemas.domain_label import (
    DomainLabelAssignmentReviewRequest,
    DomainLabelCreate,
    DomainLabelUpdate,
)
from app.services.domain_labels import DomainLabelService

router = APIRouter()
assignments_router = APIRouter()


@router.get("", summary="List domain labels for administration")
async def list_domain_labels(
    domain_labels: Annotated[
        DomainLabelService,
        Depends(get_admin_domain_label_service),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    dimension: Annotated[DomainLabelDimension | None, Query()] = None,
    status: Annotated[DomainLabelStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Any:
    result = await domain_labels.list_labels(
        page=page,
        page_size=page_size,
        dimension=dimension.value if dimension is not None else None,
        status=status.value if status is not None else None,
        q=q,
    )
    return success_response(result.items, meta=result.meta.model_dump())


@router.post("", status_code=201, summary="Create a domain label")
async def create_domain_label(
    payload: DomainLabelCreate,
    domain_labels: Annotated[
        DomainLabelService,
        Depends(get_admin_domain_label_service),
    ],
) -> Any:
    return success_response(
        await domain_labels.create_label(payload),
        status_code=201,
    )


@router.get(
    "/{label_id}/assignments",
    summary="List poem version assignments for a domain label",
)
async def list_domain_label_assignments(
    label_id: int,
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
    result = await domain_labels.list_assignments(
        label_id=label_id,
        page=page,
        page_size=page_size,
        review_status=review_status.value if review_status is not None else None,
        generation_method=(
            generation_method.value if generation_method is not None else None
        ),
    )
    return success_response(result.items, meta=result.meta.model_dump())


@router.patch("/{label_id}", summary="Update a domain label")
async def update_domain_label(
    label_id: int,
    payload: DomainLabelUpdate,
    domain_labels: Annotated[
        DomainLabelService,
        Depends(get_admin_domain_label_service),
    ],
) -> Any:
    return success_response(await domain_labels.update_label(label_id, payload))


@assignments_router.post(
    "/{assignment_id}/review",
    summary="Review a domain label assignment",
)
async def review_domain_label_assignment(
    assignment_id: int,
    payload: DomainLabelAssignmentReviewRequest,
    domain_labels: Annotated[
        DomainLabelService,
        Depends(get_admin_domain_label_service),
    ],
) -> Any:
    return success_response(
        await domain_labels.review_assignment(assignment_id, payload)
    )
