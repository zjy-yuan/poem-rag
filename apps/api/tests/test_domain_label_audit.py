from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.core.config import Settings
from app.db.base import Base
from app.db.seed import seed_catalog, seed_domain_labels
from app.db.session import create_database_engine, create_session_factory
from app.evaluation.domain_labels import DomainLabelAuditor
from app.models.domain_label import (
    DomainLabel,
    DomainLabelReviewStatus,
    DomainLabelStatus,
    PoemVersionDomainLabel,
)
from app.models.poem import Poem
from app.models.version import PoemVersion
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture()
async def audit_session(
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


async def _current_versions(
    session: AsyncSession,
    poems: list[Poem],
) -> dict[int, PoemVersion]:
    rows = await session.scalars(
        select(PoemVersion).where(
            PoemVersion.poem_id.in_([poem.id for poem in poems]),
            PoemVersion.version_no.in_([poem.version_no for poem in poems]),
        )
    )
    versions = list(rows)
    return {version.poem_id: version for version in versions}


async def _label_by_name(
    session: AsyncSession,
    name: str,
) -> DomainLabel:
    label = await session.scalar(
        select(DomainLabel).where(DomainLabel.canonical_name == name)
    )
    assert label is not None
    return label


def _assignment(
    *,
    version_id: int,
    label_id: int,
    generation_method: str,
    origin_ref: str,
    review_status: DomainLabelReviewStatus,
    source_id: int | None = None,
    model_name: str | None = None,
    task_version: str | None = None,
) -> PoemVersionDomainLabel:
    return PoemVersionDomainLabel(
        poem_version_id=version_id,
        domain_label_id=label_id,
        source_id=source_id,
        generation_method=generation_method,
        origin_ref=origin_ref,
        review_status=review_status.value,
        model_name=model_name,
        task_version=task_version,
    )


async def test_audit_summarizes_current_coverage_and_review_backlog(
    audit_session: AsyncSession,
) -> None:
    await seed_catalog(audit_session)
    await seed_domain_labels(audit_session)
    poems = list(
        (
            await audit_session.scalars(
                select(Poem).order_by(Poem.id).limit(3)
            )
        ).all()
    )
    versions = await _current_versions(audit_session, poems)
    month = await _label_by_name(audit_session, "月")
    homesickness = await _label_by_name(audit_session, "思乡")
    falling_flower = await _label_by_name(audit_session, "落花")
    flowing_water = await _label_by_name(audit_session, "流水")

    falling_flower.status = DomainLabelStatus.DEPRECATED.value
    merged_month = DomainLabel(
        dimension=month.dimension,
        canonical_name="皓月",
        normalized_name="皓月",
        status=DomainLabelStatus.MERGED.value,
        merged_into_id=month.id,
    )
    audit_session.add(merged_month)
    await audit_session.flush()

    audit_session.add_all(
        [
            _assignment(
                version_id=versions[poems[0].id].id,
                label_id=month.id,
                generation_method="manual",
                origin_ref="manual:tester:month",
                review_status=DomainLabelReviewStatus.APPROVED,
            ),
            _assignment(
                version_id=versions[poems[0].id].id,
                label_id=merged_month.id,
                generation_method="public_dataset",
                origin_ref="public_dataset:test:merged-month",
                review_status=DomainLabelReviewStatus.APPROVED,
            ),
            _assignment(
                version_id=versions[poems[0].id].id,
                label_id=homesickness.id,
                generation_method="ai",
                origin_ref="ai:test:homesickness",
                review_status=DomainLabelReviewStatus.PENDING,
                model_name="test-model",
                task_version="test-task",
            ),
            _assignment(
                version_id=versions[poems[1].id].id,
                label_id=month.id,
                generation_method="manual",
                origin_ref="manual:tester:month",
                review_status=DomainLabelReviewStatus.APPROVED,
            ),
            _assignment(
                version_id=versions[poems[1].id].id,
                label_id=falling_flower.id,
                generation_method="manual",
                origin_ref="manual:tester:deprecated",
                review_status=DomainLabelReviewStatus.APPROVED,
            ),
            _assignment(
                version_id=versions[poems[1].id].id,
                label_id=homesickness.id,
                generation_method="public_dataset",
                origin_ref="public_dataset:test:homesickness",
                review_status=DomainLabelReviewStatus.APPROVED,
            ),
            _assignment(
                version_id=versions[poems[2].id].id,
                label_id=flowing_water.id,
                generation_method="manual",
                origin_ref="dataset:invalid-prefix",
                review_status=DomainLabelReviewStatus.PENDING,
            ),
        ]
    )
    await audit_session.commit()

    report = await DomainLabelAuditor(audit_session).audit()

    assert report.scope.published_poem_count == 6
    assert report.scope.current_version_count == 6
    assert report.scope.published_poems_without_current_version == 0
    assert report.coverage.current_poems_with_any_assignment == 3
    assert report.coverage.current_poems_with_any_approved_assignment == 2
    assert report.coverage.current_poems_with_online_visible_label == 2
    assert report.coverage.current_poems_without_any_assignment == 3
    assert report.coverage.current_poems_without_online_visible_label == 4
    assert report.coverage.online_visible_coverage_rate == pytest.approx(
        2 / 6,
        abs=0.000001,
    )
    assert report.coverage.dimensions["imagery"].total_label_count == 7
    assert report.coverage.dimensions["imagery"].active_label_count == 5
    assert report.coverage.dimensions["imagery"].online_label_count == 1
    assert report.coverage.dimensions["imagery"].covered_poem_count == 2
    assert report.coverage.dimensions["emotion"].online_label_count == 1
    assert report.coverage.dimensions["emotion"].covered_poem_count == 1
    assert report.coverage.dimensions["theme"].covered_poem_count == 0
    assert report.coverage.dimensions["allusion"].covered_poem_count == 0
    assert report.review.current_assignment_count == 7
    assert report.review.review_status_counts == {
        "pending": 2,
        "approved": 5,
        "rejected": 0,
        "archived": 0,
    }
    assert report.review.generation_method_counts == {
        "manual": 4,
        "public_dataset": 2,
        "ai": 1,
    }
    assert report.review.label_status_counts == {
        "active": 17,
        "merged": 1,
        "deprecated": 1,
    }
    assert report.distributions.current_assignments_per_poem.maximum == 3
    assert report.distributions.online_visible_labels_per_poem.maximum == 2
    assert report.risks.approved_assignments_with_merged_labels == 1
    assert report.risks.approved_assignments_with_deprecated_labels == 1
    assert report.risks.active_labels_without_online_visible_assignments == 15
    assert report.risks.public_or_ai_assignments_without_source == 3
    assert report.risks.ai_assignments_missing_metadata == 0
    assert report.risks.invalid_merge_targets == 0
    assert report.risks.origin_ref_generation_method_mismatches == 1


async def test_audit_identifies_invalid_merge_and_ai_source_metadata_risks(
    audit_session: AsyncSession,
) -> None:
    await seed_catalog(audit_session)
    await seed_domain_labels(audit_session)
    poem = await audit_session.scalar(select(Poem).order_by(Poem.id))
    assert poem is not None
    version = await audit_session.scalar(
        select(PoemVersion).where(
            PoemVersion.poem_id == poem.id,
            PoemVersion.version_no == poem.version_no,
        )
    )
    assert version is not None
    month = await _label_by_name(audit_session, "月")
    invalid_merge = DomainLabel(
        dimension=month.dimension,
        canonical_name="残月",
        normalized_name="残月",
        status=DomainLabelStatus.MERGED.value,
        merged_into_id=None,
    )
    audit_session.add(invalid_merge)
    await audit_session.flush()
    audit_session.add(
        _assignment(
            version_id=version.id,
            label_id=invalid_merge.id,
            generation_method="ai",
            origin_ref="ai:test:invalid-merge",
            review_status=DomainLabelReviewStatus.APPROVED,
            model_name="test-model",
            task_version="test-task",
        )
    )
    await audit_session.commit()

    report = await DomainLabelAuditor(audit_session).audit()

    assert report.coverage.current_poems_with_online_visible_label == 0
    assert report.risks.approved_assignments_with_merged_labels == 1
    assert report.risks.public_or_ai_assignments_without_source == 1
    assert report.risks.invalid_merge_targets == 1
