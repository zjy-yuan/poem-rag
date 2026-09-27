from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.db.seed import seed_domain_labels
from app.models.domain_label import (
    DomainLabel,
    DomainLabelAlias,
    PoemVersionDomainLabel,
)
from app.models.poem import Poem
from app.models.source import PoemSource
from app.models.version import PoemVersion
from app.schemas.corpus_import import CorpusImportDataset
from app.schemas.domain_label_import import DomainLabelImportDataset
from app.services.corpus_import import CorpusImportService
from app.services.domain_label_import import DomainLabelImportService
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

POEM_SOURCE_KEY = "open-poetry-fixture"
POEM_EXTERNAL_ID = "quiet-night-001"
EXAMPLE_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "import"
    / "example_domain_labels_v1.json"
)


def _corpus_dataset(*, external_id: str = POEM_EXTERNAL_ID) -> CorpusImportDataset:
    return CorpusImportDataset.model_validate(
        {
            "version": "corpus-fixture-v1",
            "source": {
                "source_key": POEM_SOURCE_KEY,
                "source_type": "file",
                "source_name": "开放许可测试语料",
                "source_url": "https://example.com/open-poetry",
                "license_note": "仅用于测试的固定许可说明",
            },
            "defaults": {"publish": True},
            "records": [
                {
                    "external_id": external_id,
                    "title": "静夜思",
                    "content": (
                        "床前明月光，疑是地上霜。\n"
                        "举头望明月，低头思故乡。"
                    ),
                }
            ],
        }
    )


def _label_payload(
    *,
    external_id: str = POEM_EXTERNAL_ID,
    labels: list[dict[str, Any]] | None = None,
    generation_method: str = "public_dataset",
    model_name: str | None = None,
    task_version: str | None = None,
) -> dict[str, Any]:
    return {
        "version": "domain-label-fixture-v1",
        "source": {
            "source_key": "domain-label-fixture",
            "source_name": "领域标签测试集",
            "source_url": "https://example.com/domain-labels",
            "license_note": "仅用于测试的固定许可说明",
        },
        "generation_method": generation_method,
        "model_name": model_name,
        "task_version": task_version,
        "poem_source_key": POEM_SOURCE_KEY,
        "records": [
            {
                "external_id": external_id,
                "labels": labels
                or [
                    {
                        "dimension": "imagery",
                        "name": "月亮",
                        "confidence": "0.9",
                        "evidence_text": "床前明月光",
                        "line_start": 1,
                        "line_end": 1,
                    },
                    {
                        "dimension": "emotion",
                        "name": "想家",
                        "confidence": "0.8",
                        "evidence_text": "低头思故乡",
                        "line_start": 2,
                        "line_end": 2,
                    },
                ],
            }
        ],
    }


def _label_dataset(**kwargs: Any) -> DomainLabelImportDataset:
    return DomainLabelImportDataset.model_validate(_label_payload(**kwargs))


async def _prepare_corpus(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_id: str = POEM_EXTERNAL_ID,
) -> int:
    async with session_factory() as session:
        await seed_domain_labels(session)
        report = await CorpusImportService(session).import_dataset(
            _corpus_dataset(external_id=external_id)
        )
    assert report.created_records == 1, report.records[0].message
    poem_id = report.records[0].poem_id
    assert poem_id is not None
    return poem_id


async def _import_labels(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: DomainLabelImportDataset,
    dry_run: bool = False,
) -> Any:
    async with session_factory() as session:
        return await DomainLabelImportService(session).import_dataset(
            dataset,
            dry_run=dry_run,
        )


async def _load_assignments(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[Any, ...]]:
    async with session_factory() as session:
        result = await session.execute(
            select(
                PoemVersionDomainLabel.id,
                DomainLabel.canonical_name,
                PoemVersionDomainLabel.generation_method,
                PoemVersionDomainLabel.review_status,
                PoemVersionDomainLabel.source_id,
                PoemVersionDomainLabel.origin_ref,
                PoemVersionDomainLabel.poem_version_id,
                PoemVersionDomainLabel.model_name,
                PoemVersionDomainLabel.task_version,
                PoemVersion.version_no,
            )
            .join(
                DomainLabel,
                DomainLabel.id == PoemVersionDomainLabel.domain_label_id,
            )
            .join(
                PoemVersion,
                PoemVersion.id == PoemVersionDomainLabel.poem_version_id,
            )
            .order_by(PoemVersionDomainLabel.id.asc())
        )
        return [tuple(row) for row in result.all()]


async def _load_source_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with session_factory() as session:
        source_id = await session.scalar(
            select(PoemSource.id).where(
                PoemSource.source_key == POEM_SOURCE_KEY,
                PoemSource.external_id == POEM_EXTERNAL_ID,
            )
        )
    assert source_id is not None
    return source_id


async def _load_current_version_id(
    session_factory: async_sessionmaker[AsyncSession],
    poem_id: int,
) -> int:
    async with session_factory() as session:
        version_id = await session.scalar(
            select(PoemVersion.id)
            .join(
                Poem,
                Poem.id == PoemVersion.poem_id,
            )
            .where(
                PoemVersion.poem_id == poem_id,
                PoemVersion.version_no == Poem.version_no,
            )
        )
    assert version_id is not None
    return version_id


async def _set_review_statuses(
    session_factory: async_sessionmaker[AsyncSession],
    statuses: dict[int, str],
) -> None:
    async with session_factory() as session:
        for assignment_id, status in statuses.items():
            assignment = await session.get(PoemVersionDomainLabel, assignment_id)
            assert assignment is not None
            assignment.review_status = status
        await session.commit()


async def _add_conflicting_alias(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        label = DomainLabel(
            dimension="imagery",
            canonical_name="明月",
            normalized_name="明月",
            status="active",
        )
        session.add(label)
        await session.flush()
        session.add(
            DomainLabelAlias(
                domain_label_id=label.id,
                alias="月亮",
                normalized_alias="月亮",
            )
        )
        await session.commit()


def test_domain_label_import_dry_run_is_read_only(client: TestClient) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    poem_id = portal.call(_prepare_corpus, session_factory)
    dataset = _label_dataset()

    report = portal.call(_import_labels, session_factory, dataset, True)

    assert report.dry_run is True
    assert report.created_records == 1
    assert report.created_assignments == 2
    assert report.records[0].poem_id == poem_id
    assert portal.call(_load_assignments, session_factory) == []


def test_domain_label_import_creates_pending_canonical_assignments(
    client: TestClient,
) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    poem_id = portal.call(_prepare_corpus, session_factory)
    source_id = portal.call(_load_source_id, session_factory)
    version_id = portal.call(_load_current_version_id, session_factory, poem_id)

    report = portal.call(_import_labels, session_factory, _label_dataset())

    assert report.created_records == 1
    assert report.created_assignments == 2
    assert report.failed_assignments == 0
    rows = portal.call(_load_assignments, session_factory)
    assert {row[1] for row in rows} == {"月", "思乡"}
    assert {row[2] for row in rows} == {"public_dataset"}
    assert {row[3] for row in rows} == {"pending"}
    assert {row[4] for row in rows} == {source_id}
    assert {row[6] for row in rows} == {version_id}
    assert {row[9] for row in rows} == {1}
    assert all(":月" in row[5] or ":思乡" in row[5] for row in rows)


def test_domain_label_import_is_idempotent_and_preserves_review(
    client: TestClient,
) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    portal.call(_prepare_corpus, session_factory)
    dataset = _label_dataset()
    first = portal.call(_import_labels, session_factory, dataset)
    assert first.created_assignments == 2
    rows = portal.call(_load_assignments, session_factory)
    assert len(rows) == 2
    portal.call(
        _set_review_statuses,
        session_factory,
        {rows[0][0]: "approved", rows[1][0]: "rejected"},
    )

    second = portal.call(_import_labels, session_factory, dataset)

    assert second.unchanged_records == 1
    assert second.created_assignments == 0
    assert second.unchanged_assignments == 2
    refreshed = portal.call(_load_assignments, session_factory)
    assert [row[3] for row in refreshed] == ["approved", "rejected"]
    assert len(refreshed) == 2


def test_domain_label_import_isolates_failed_records(client: TestClient) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    portal.call(_prepare_corpus, session_factory)
    payload = _label_payload()
    payload["records"] = [
        {
            "external_id": "missing-poem-001",
            "labels": [
                {
                    "dimension": "imagery",
                    "name": "月亮",
                }
            ],
        },
        payload["records"][0],
    ]
    dataset = DomainLabelImportDataset.model_validate(payload)

    report = portal.call(_import_labels, session_factory, dataset)

    assert report.failed_records == 1
    assert report.created_records == 1
    assert [item.status for item in report.records] == ["failed", "created"]
    assert "未找到对应作品来源" in (report.records[0].message or "")
    assert len(portal.call(_load_assignments, session_factory)) == 2


def test_domain_label_import_rejects_unknown_and_ambiguous_labels(
    client: TestClient,
) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    portal.call(_prepare_corpus, session_factory)
    unknown = _label_dataset(
        labels=[{"dimension": "imagery", "name": "不存在的意象"}]
    )

    unknown_report = portal.call(_import_labels, session_factory, unknown)

    assert unknown_report.failed_records == 1
    assert "标签不存在或未启用" in (unknown_report.records[0].message or "")
    assert portal.call(_load_assignments, session_factory) == []

    portal.call(_add_conflicting_alias, session_factory)
    ambiguous_report = portal.call(
        _import_labels,
        session_factory,
        _label_dataset(labels=[{"dimension": "imagery", "name": "月亮"}]),
    )

    assert ambiguous_report.failed_records == 1
    assert "存在歧义" in (ambiguous_report.records[0].message or "")
    assert portal.call(_load_assignments, session_factory) == []


def test_domain_label_import_deduplicates_resolved_aliases(
    client: TestClient,
) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    portal.call(_prepare_corpus, session_factory)
    dataset = _label_dataset(
        labels=[
            {"dimension": "imagery", "name": "月亮"},
            {"dimension": "imagery", "name": "月"},
        ]
    )

    report = portal.call(_import_labels, session_factory, dataset)

    assert report.created_assignments == 1
    rows = portal.call(_load_assignments, session_factory)
    assert len(rows) == 1
    assert rows[0][1] == "月"
    assert rows[0][5].endswith(":imagery:月")


def test_domain_label_import_requires_and_records_ai_metadata(
    client: TestClient,
) -> None:
    invalid_payload = _label_payload(generation_method="ai")
    with pytest.raises(ValidationError, match="model_name"):
        DomainLabelImportDataset.model_validate(invalid_payload)

    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    portal.call(_prepare_corpus, session_factory)
    dataset = _label_dataset(
        generation_method="ai",
        model_name="deepseek-chat",
        task_version="domain-label-v1",
    )

    report = portal.call(_import_labels, session_factory, dataset)

    assert report.created_assignments == 2
    rows = portal.call(_load_assignments, session_factory)
    assert {row[7] for row in rows} == {"deepseek-chat"}
    assert {row[8] for row in rows} == {"domain-label-v1"}


def test_domain_label_import_schema_rejects_duplicate_and_long_origin() -> None:
    duplicate = _label_payload()
    duplicate["records"].append(dict(duplicate["records"][0]))
    with pytest.raises(ValidationError, match="external_id"):
        DomainLabelImportDataset.model_validate(duplicate)

    too_long = _label_payload()
    too_long["records"][0]["external_id"] = "x" * 255
    with pytest.raises(ValidationError, match="origin_ref"):
        DomainLabelImportDataset.model_validate(too_long)

    invalid_range = _label_payload()
    invalid_range["records"][0]["labels"][0]["line_start"] = 2
    invalid_range["records"][0]["labels"][0]["line_end"] = 1
    with pytest.raises(ValidationError, match="line_end"):
        DomainLabelImportDataset.model_validate(invalid_range)


def test_example_domain_label_dataset_is_valid() -> None:
    payload = json.loads(EXAMPLE_DATASET_PATH.read_text(encoding="utf-8"))

    dataset = DomainLabelImportDataset.model_validate(payload)

    assert dataset.poem_source_key == "aopao-chinese-gushiwen"
    assert len(dataset.records) == 5
