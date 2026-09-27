from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.core.config import Settings
from app.core.text import normalize_lookup
from app.db.base import Base
from app.db.seed import DOMAIN_LABELS, seed_catalog, seed_domain_labels
from app.db.session import create_database_engine, create_session_factory
from app.models.domain_label import (
    DomainLabel,
    DomainLabelAlias,
    DomainLabelReviewStatus,
    DomainLabelStatus,
    PoemVersionDomainLabel,
)
from app.models.poem import Poem
from app.models.user import User, UserRole
from app.models.version import PoemVersion
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture()
async def domain_label_session(
    api_settings: Settings,
) -> AsyncIterator[AsyncSession]:
    import app.models  # noqa: F401

    engine = create_database_engine(api_settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def _expected_alias_count() -> int:
    return sum(len(item["aliases"]) for item in DOMAIN_LABELS)


async def test_seed_domain_labels_is_idempotent(
    domain_label_session: AsyncSession,
) -> None:
    first = await seed_domain_labels(domain_label_session)
    second = await seed_domain_labels(domain_label_session)

    assert first == {
        "labels": len(DOMAIN_LABELS),
        "existing_labels": 0,
        "aliases": _expected_alias_count(),
    }
    assert second == {
        "labels": 0,
        "existing_labels": len(DOMAIN_LABELS),
        "aliases": 0,
    }
    assert await domain_label_session.scalar(select(func.count(DomainLabel.id))) == len(
        DOMAIN_LABELS
    )
    assert (
        await domain_label_session.scalar(select(func.count(DomainLabelAlias.id)))
        == _expected_alias_count()
    )


async def test_seed_domain_labels_does_not_reactivate_deprecated_label(
    domain_label_session: AsyncSession,
) -> None:
    await seed_domain_labels(domain_label_session)
    label = await domain_label_session.scalar(
        select(DomainLabel).where(DomainLabel.normalized_name == normalize_lookup("月"))
    )
    assert label is not None
    label.status = DomainLabelStatus.DEPRECATED.value
    alias = await domain_label_session.scalar(
        select(DomainLabelAlias).where(DomainLabelAlias.domain_label_id == label.id)
    )
    assert alias is not None
    await domain_label_session.delete(alias)
    await domain_label_session.commit()

    result = await seed_domain_labels(domain_label_session)

    assert result["labels"] == 0
    assert result["aliases"] == 0
    refreshed = await domain_label_session.get(DomainLabel, label.id)
    assert refreshed is not None
    assert refreshed.status == DomainLabelStatus.DEPRECATED.value
    assert (
        await domain_label_session.scalar(
            select(func.count(DomainLabelAlias.id)).where(
                DomainLabelAlias.domain_label_id == label.id
            )
        )
        == 2
    )


async def test_ai_domain_label_requires_model_and_task_version(
    domain_label_session: AsyncSession,
) -> None:
    await seed_catalog(domain_label_session)
    await seed_domain_labels(domain_label_session)
    poem = await domain_label_session.scalar(select(Poem).order_by(Poem.id))
    assert poem is not None
    version = await domain_label_session.scalar(
        select(PoemVersion).where(PoemVersion.poem_id == poem.id)
    )
    assert version is not None
    label = await domain_label_session.scalar(select(DomainLabel).order_by(DomainLabel.id))
    assert label is not None

    domain_label_session.add(
        PoemVersionDomainLabel(
            poem_version_id=version.id,
            domain_label_id=label.id,
            generation_method="ai",
            origin_ref="ai:test",
            review_status=DomainLabelReviewStatus.PENDING.value,
        )
    )

    with pytest.raises(IntegrityError):
        await domain_label_session.commit()
    await domain_label_session.rollback()


async def test_domain_label_assignment_defaults_to_pending(
    domain_label_session: AsyncSession,
) -> None:
    await seed_catalog(domain_label_session)
    await seed_domain_labels(domain_label_session)
    poem = await domain_label_session.scalar(select(Poem).order_by(Poem.id))
    assert poem is not None
    version = await domain_label_session.scalar(
        select(PoemVersion).where(PoemVersion.poem_id == poem.id)
    )
    assert version is not None
    label = await domain_label_session.scalar(select(DomainLabel).order_by(DomainLabel.id))
    assert label is not None
    assignment = PoemVersionDomainLabel(
        poem_version_id=version.id,
        domain_label_id=label.id,
        generation_method="manual",
        origin_ref="manual:test",
    )
    domain_label_session.add(assignment)
    await domain_label_session.commit()

    stored = await domain_label_session.get(PoemVersionDomainLabel, assignment.id)
    assert stored is not None
    assert stored.review_status == DomainLabelReviewStatus.PENDING.value


def _register(client: TestClient, email: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Domain label tester",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _promote_user(
    session_factory: async_sessionmaker[AsyncSession],
    email: str,
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.email == email).values(role=UserRole.ADMIN.value)
        )
        await session.commit()


def _admin_headers(client: TestClient) -> dict[str, str]:
    email = "domain-label-admin@example.com"
    body = _register(client, email)
    portal = client.portal
    assert portal is not None
    portal.call(_promote_user, client.app.state.session_factory, email)
    token = body["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_domain_label_admin_review_contract(client: TestClient) -> None:
    reader = _register(client, "domain-label-reader@example.com")
    reader_headers = {"Authorization": f"Bearer {reader['data']['access_token']}"}
    forbidden = client.post(
        "/api/v1/admin/domain-labels",
        headers=reader_headers,
        json={"dimension": "imagery", "canonical_name": "明月"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "AUTH_FORBIDDEN"

    headers = _admin_headers(client)
    label_response = client.post(
        "/api/v1/admin/domain-labels",
        headers=headers,
        json={
            "dimension": "imagery",
            "canonical_name": "明月",
            "description": "思乡与怀人的常见意象",
            "aliases": ["月亮", "Moon", "moon"],
        },
    )
    assert label_response.status_code == 201
    label = label_response.json()["data"]
    assert [item["alias"] for item in label["aliases"]] == [
        "月亮",
        "Moon",
    ]

    duplicate = client.post(
        "/api/v1/admin/domain-labels",
        headers=headers,
        json={"dimension": "imagery", "canonical_name": "明月"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DOMAIN_LABEL_EXISTS"

    poem_response = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "静夜思",
            "content": "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。",
        },
    )
    assert poem_response.status_code == 201
    poem_id = poem_response.json()["data"]["id"]
    assert (
        client.post(
            f"/api/v1/admin/poems/{poem_id}/publish",
            headers=headers,
        ).status_code
        == 200
    )

    assignment_response = client.post(
        f"/api/v1/admin/poems/{poem_id}/domain-labels",
        headers=headers,
        json={
            "domain_label_id": label["id"],
            "generation_method": "manual",
            "evidence_text": "举头望明月，低头思故乡。",
            "line_start": 1,
            "line_end": 1,
        },
    )
    assert assignment_response.status_code == 201
    assignment = assignment_response.json()["data"]
    assert assignment["review_status"] == "pending"
    assert assignment["version_no"] == 1
    assert assignment["label"]["canonical_name"] == "明月"

    repeated = client.post(
        f"/api/v1/admin/poems/{poem_id}/domain-labels",
        headers=headers,
        json={
            "domain_label_id": label["id"],
            "generation_method": "manual",
            "evidence_text": "举头望明月，低头思故乡。",
            "line_start": 1,
            "line_end": 1,
        },
    )
    assert repeated.status_code == 200
    assert repeated.json()["data"]["id"] == assignment["id"]

    public_before_review = client.get(
        f"/api/v1/poems/{poem_id}/domain-labels"
    ).json()
    assert public_before_review["data"] == []

    approved = client.post(
        f"/api/v1/admin/domain-label-assignments/{assignment['id']}/review",
        headers=headers,
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["review_status"] == "approved"
    assert approved.json()["data"]["reviewed_by_id"] is not None
    assert approved.json()["data"]["reviewed_at"] is not None

    public_after_review = client.get(
        f"/api/v1/poems/{poem_id}/domain-labels"
    ).json()
    assert [item["canonical_name"] for item in public_after_review["data"]] == ["明月"]
    assert public_after_review["data"][0]["generation_method"] == "manual"

    invalid_transition = client.post(
        f"/api/v1/admin/domain-label-assignments/{assignment['id']}/review",
        headers=headers,
        json={"action": "approve"},
    )
    assert invalid_transition.status_code == 409
    assert (
        invalid_transition.json()["error"]["code"]
        == "DOMAIN_LABEL_INVALID_REVIEW_TRANSITION"
    )

    ai_without_metadata = client.post(
        f"/api/v1/admin/poems/{poem_id}/domain-labels",
        headers=headers,
        json={
            "domain_label_id": label["id"],
            "generation_method": "ai",
            "origin_ref": "ai:test:missing-metadata",
        },
    )
    assert ai_without_metadata.status_code == 422
    assert ai_without_metadata.json()["error"]["code"] == "VALIDATION_ERROR"

    assignments = client.get(
        f"/api/v1/admin/domain-labels/{label['id']}/assignments",
        headers=headers,
    ).json()
    assert assignments["meta"]["total"] == 1
    assert assignments["data"][0]["poem_id"] == poem_id

    poem_assignments = client.get(
        f"/api/v1/admin/poems/{poem_id}/domain-labels",
        headers=headers,
        params={"review_status": "approved"},
    )
    assert poem_assignments.status_code == 200
    poem_assignments_body = poem_assignments.json()
    assert poem_assignments_body["meta"]["total"] == 1
    assert poem_assignments_body["data"][0]["id"] == assignment["id"]
    assert poem_assignments_body["data"][0]["version_no"] == 1

    pending_poem_assignments = client.get(
        f"/api/v1/admin/poems/{poem_id}/domain-labels",
        headers=headers,
        params={"review_status": "pending"},
    ).json()
    assert pending_poem_assignments["meta"]["total"] == 0

    target_response = client.post(
        "/api/v1/admin/domain-labels",
        headers=headers,
        json={
            "dimension": "imagery",
            "canonical_name": "月",
            "aliases": ["皓月"],
        },
    )
    assert target_response.status_code == 201
    target = target_response.json()["data"]

    merged = client.patch(
        f"/api/v1/admin/domain-labels/{label['id']}",
        headers=headers,
        json={"status": "merged", "merged_into_id": target["id"]},
    )
    assert merged.status_code == 200
    assert merged.json()["data"]["status"] == "merged"
    assert merged.json()["data"]["merged_into_id"] == target["id"]

    public_after_merge = client.get(
        f"/api/v1/poems/{poem_id}/domain-labels"
    ).json()
    assert [item["canonical_name"] for item in public_after_merge["data"]] == ["月"]

    deprecated = client.patch(
        f"/api/v1/admin/domain-labels/{target['id']}",
        headers=headers,
        json={"status": "deprecated"},
    )
    assert deprecated.status_code == 200
    assert deprecated.json()["data"]["status"] == "deprecated"
    assert deprecated.json()["data"]["merged_into_id"] is None
    assert (
        client.get(f"/api/v1/poems/{poem_id}/domain-labels").json()["data"] == []
    )
