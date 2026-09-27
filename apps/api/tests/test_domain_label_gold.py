from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from app.core.config import Settings
from app.core.text import sha256_text
from app.db.base import Base
from app.db.seed import seed_domain_labels
from app.db.session import create_database_engine, create_session_factory
from app.evaluation.domain_label_gold import (
    DomainLabelGoldEvaluator,
    _metric_from_counts,
)
from app.models.domain_label import (
    DomainLabel,
    DomainLabelReviewStatus,
    DomainLabelStatus,
    PoemVersionDomainLabel,
)
from app.models.poem import Poem
from app.models.version import PoemVersion
from app.schemas.corpus_import import CorpusImportDataset
from app.schemas.domain_label_gold import DomainLabelGoldDataset
from app.services.corpus_import import CorpusImportService
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

POEM_SOURCE_KEY = "gold-fixture"
POEM_EXTERNAL_ID = "gold-poem-001"
POEM_CONTENT = "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。"
POEM_CONTENT_HASH = sha256_text(POEM_CONTENT)
GOLD_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "domain_label_gold_v1.json"
)


@pytest.fixture()
async def gold_session(
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


def _corpus_dataset() -> CorpusImportDataset:
    return CorpusImportDataset.model_validate(
        {
            "version": "gold-fixture-corpus-v1",
            "source": {
                "source_key": POEM_SOURCE_KEY,
                "source_type": "file",
                "source_name": "金标准测试语料",
                "source_url": "https://example.com/gold-fixture",
                "license_note": "仅用于测试的固定许可说明",
            },
            "defaults": {"publish": True},
            "records": [
                {
                    "external_id": POEM_EXTERNAL_ID,
                    "title": "静夜思",
                    "content": POEM_CONTENT,
                }
            ],
        }
    )


def _gold_label(
    *,
    dimension: str,
    name: str,
    severity: str = "normal",
    evidence_text: str = "低头思故乡",
    line_start: int = 2,
    line_end: int = 2,
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "name": name,
        "severity": severity,
        "evidence": [
            {
                "evidence_text": evidence_text,
                "line_start": line_start,
                "line_end": line_end,
            }
        ],
    }


def _gold_payload(
    *,
    labels: list[dict[str, Any]] | None = None,
    expected_hash: str = POEM_CONTENT_HASH,
    external_id: str = POEM_EXTERNAL_ID,
) -> dict[str, Any]:
    return {
        "version": "gold-fixture-v1",
        "name": "金标准评估测试集",
        "description": "用于验证标签评估器的固定金标准数据集。",
        "selection_strategy": "固定单首作品，人工确认月与思乡。",
        "source": {
            "source_key": "project-manual-gold-standard",
            "source_name": "项目人工金标准样本",
            "source_url": None,
            "license_note": "仅用于测试。",
        },
        "poem_source_key": POEM_SOURCE_KEY,
        "target_record_counts": {
            "imagery": 1,
            "emotion": 1,
            "theme": 0,
            "allusion": 0,
        },
        "records": [
            {
                "external_id": external_id,
                "title": "静夜思",
                "author": "李白",
                "expected_source_content_hash": expected_hash,
                "labels": labels
                if labels is not None
                else [
                    _gold_label(
                        dimension="imagery",
                        name="月",
                        severity="critical",
                        evidence_text=POEM_CONTENT,
                        line_start=1,
                        line_end=2,
                    ),
                    _gold_label(
                        dimension="emotion",
                        name="思乡",
                        severity="critical",
                    ),
                ],
            }
        ],
    }


def _gold_dataset(**kwargs: Any) -> DomainLabelGoldDataset:
    return DomainLabelGoldDataset.model_validate(_gold_payload(**kwargs))


async def _prepare_corpus(session: AsyncSession) -> tuple[int, int]:
    await seed_domain_labels(session)
    report = await CorpusImportService(session).import_dataset(_corpus_dataset())
    assert report.created_records == 1, report.records[0].message
    poem_id = report.records[0].poem_id
    assert poem_id is not None
    version_id = await session.scalar(
        select(PoemVersion.id)
        .join(Poem, Poem.id == PoemVersion.poem_id)
        .where(
            PoemVersion.poem_id == poem_id,
            PoemVersion.version_no == Poem.version_no,
        )
    )
    assert version_id is not None
    return poem_id, version_id


async def _label_by_name(session: AsyncSession, name: str) -> DomainLabel:
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
    review_status: DomainLabelReviewStatus = DomainLabelReviewStatus.APPROVED,
    evidence_text: str | None = "低头思故乡",
    line_start: int | None = 2,
    line_end: int | None = 2,
    model_name: str | None = None,
    task_version: str | None = None,
) -> PoemVersionDomainLabel:
    return PoemVersionDomainLabel(
        poem_version_id=version_id,
        domain_label_id=label_id,
        generation_method=generation_method,
        origin_ref=origin_ref,
        review_status=review_status.value,
        evidence_text=evidence_text,
        line_start=line_start,
        line_end=line_end,
        model_name=model_name,
        task_version=task_version,
    )


async def _approve_matching_labels(
    session: AsyncSession,
    version_id: int,
) -> None:
    month = await _label_by_name(session, "月")
    homesickness = await _label_by_name(session, "思乡")
    session.add_all(
        [
            _assignment(
                version_id=version_id,
                label_id=month.id,
                generation_method="manual",
                origin_ref="manual:gold:month",
                evidence_text=POEM_CONTENT,
                line_start=1,
                line_end=2,
            ),
            _assignment(
                version_id=version_id,
                label_id=homesickness.id,
                generation_method="manual",
                origin_ref="manual:gold:homesickness",
            ),
        ]
    )
    await session.commit()


def test_gold_dataset_schema_rejects_invalid_payloads() -> None:
    duplicate = _gold_payload()
    duplicate["records"][0]["labels"].append(
        _gold_label(dimension="emotion", name="思乡")
    )
    with pytest.raises(ValidationError, match="重复标签"):
        DomainLabelGoldDataset.model_validate(duplicate)

    invalid_range = _gold_payload()
    invalid_range["records"][0]["labels"][0]["evidence"][0]["line_start"] = 3
    invalid_range["records"][0]["labels"][0]["evidence"][0]["line_end"] = 1
    with pytest.raises(ValidationError, match="line_end"):
        DomainLabelGoldDataset.model_validate(invalid_range)

    missing_dimension = _gold_payload()
    del missing_dimension["target_record_counts"]["allusion"]
    with pytest.raises(ValidationError, match="四个标签维度"):
        DomainLabelGoldDataset.model_validate(missing_dimension)

    missing_hash = _gold_payload()
    del missing_hash["records"][0]["expected_source_content_hash"]
    with pytest.raises(ValidationError, match="expected_source_content_hash"):
        DomainLabelGoldDataset.model_validate(missing_hash)


def test_gold_dataset_schema_rejects_duplicate_records_and_empty_labels() -> None:
    duplicated = _gold_payload()
    duplicated["records"].append(dict(duplicated["records"][0]))
    with pytest.raises(ValidationError, match="external_id"):
        DomainLabelGoldDataset.model_validate(duplicated)

    unlabeled = _gold_payload(labels=[])
    with pytest.raises(ValidationError, match="至少需要一条标签"):
        DomainLabelGoldDataset.model_validate(unlabeled)


def test_metric_from_counts_reports_zero_when_prediction_or_gold_is_empty() -> None:
    empty_prediction = _metric_from_counts(0, 0, 3, gold_count=3, predicted_count=0)
    assert (empty_prediction.precision, empty_prediction.recall) == (0.0, 0.0)
    assert empty_prediction.f1 == 0.0

    empty_gold = _metric_from_counts(0, 2, 0, gold_count=0, predicted_count=2)
    assert (empty_gold.precision, empty_gold.recall) == (0.0, 0.0)
    assert empty_gold.f1 == 0.0

    empty_both = _metric_from_counts(0, 0, 0, gold_count=0, predicted_count=0)
    assert (empty_both.precision, empty_both.recall, empty_both.f1) == (0.0, 0.0, 0.0)

    balanced = _metric_from_counts(2, 1, 1)
    assert balanced.precision == pytest.approx(2 / 3, abs=0.000001)
    assert balanced.recall == pytest.approx(2 / 3, abs=0.000001)
    assert balanced.f1 == pytest.approx(2 / 3, abs=0.000001)


def test_build_metrics_computes_micro_macro_and_skips_empty_dimensions() -> None:
    gold = {
        "imagery": {("a", 1), ("a", 2)},
        "emotion": {("a", 3)},
    }
    predicted = {
        "imagery": {("a", 1)},
        "emotion": {("a", 3), ("a", 4)},
    }

    summary = DomainLabelGoldEvaluator._build_metrics(gold, predicted)

    assert summary.macro_dimension_count == 2
    assert summary.micro.true_positive == 2
    assert summary.micro.false_positive == 1
    assert summary.micro.false_negative == 1
    assert summary.micro.f1 == pytest.approx(2 / 3, abs=0.000001)
    assert summary.macro.precision == pytest.approx(0.75, abs=0.000001)
    assert summary.macro.recall == pytest.approx(0.75, abs=0.000001)
    assert summary.dimensions["imagery"].recall == pytest.approx(0.5, abs=0.000001)
    assert summary.dimensions["emotion"].precision == pytest.approx(0.5, abs=0.000001)
    assert summary.dimensions["theme"].precision == 0.0
    assert summary.dimensions["allusion"].f1 == 0.0


async def test_evaluation_reports_true_positives_without_missing_labels(
    gold_session: AsyncSession,
) -> None:
    _, version_id = await _prepare_corpus(gold_session)
    await _approve_matching_labels(gold_session, version_id)

    report = await DomainLabelGoldEvaluator(gold_session).evaluate(_gold_dataset())

    assert report.metrics.micro.precision == 1.0
    assert report.metrics.micro.recall == 1.0
    assert report.metrics.micro.f1 == 1.0
    assert report.coverage.published_poem_count == 1
    assert report.coverage.records_with_predictions == 1
    assert report.coverage.records_without_predictions == 0
    assert report.quality.critical_gold_label_count == 2
    assert report.quality.critical_missing_label_count == 0
    assert report.quality.critical_error_rate == 0.0
    assert report.quality.evidence_error_count == 0
    assert report.quality.approved_assignment_count == 2
    assert report.records[0].missing_labels == []
    assert report.records[0].extra_labels == []


async def test_evaluation_flags_critical_missing_and_normal_missing_labels(
    gold_session: AsyncSession,
) -> None:
    _, version_id = await _prepare_corpus(gold_session)
    month = await _label_by_name(gold_session, "月")
    gold_session.add(
        _assignment(
            version_id=version_id,
            label_id=month.id,
            generation_method="manual",
            origin_ref="manual:gold:month",
            evidence_text=POEM_CONTENT,
            line_start=1,
            line_end=2,
        )
    )
    await gold_session.commit()
    dataset = _gold_dataset(
        labels=[
            _gold_label(
                dimension="imagery",
                name="月",
                severity="critical",
                evidence_text=POEM_CONTENT,
                line_start=1,
                line_end=2,
            ),
            _gold_label(dimension="emotion", name="思乡", severity="critical"),
            _gold_label(dimension="emotion", name="离别"),
        ]
    )

    report = await DomainLabelGoldEvaluator(gold_session).evaluate(dataset)

    assert report.quality.gold_label_count == 3
    assert report.quality.critical_gold_label_count == 2
    assert report.quality.false_negative == 2
    assert report.quality.critical_missing_label_count == 1
    assert report.quality.critical_error_rate == 0.5
    assert [item.canonical_name for item in report.records[0].missing_labels] == [
        "思乡",
        "离别",
    ]


async def test_evaluation_reports_extra_predicted_labels(
    gold_session: AsyncSession,
) -> None:
    _, version_id = await _prepare_corpus(gold_session)
    await _approve_matching_labels(gold_session, version_id)
    extra = DomainLabel(
        dimension="imagery",
        canonical_name="孤舟",
        normalized_name="孤舟",
        status=DomainLabelStatus.ACTIVE.value,
    )
    gold_session.add(extra)
    await gold_session.flush()
    gold_session.add(
        _assignment(
            version_id=version_id,
            label_id=extra.id,
            generation_method="public_dataset",
            origin_ref="public_dataset:gold:geese",
        )
    )
    await gold_session.commit()
    dataset = _gold_dataset(
        labels=[
            _gold_label(dimension="imagery", name="月"),
            _gold_label(dimension="emotion", name="思乡"),
        ]
    )

    report = await DomainLabelGoldEvaluator(gold_session).evaluate(dataset)

    assert report.metrics.micro.false_positive == 1
    assert report.metrics.micro.precision == pytest.approx(2 / 3, abs=0.000001)
    assert [item.canonical_name for item in report.records[0].extra_labels] == ["孤舟"]
    assert report.records[0].extra_labels[0].generation_method == "public_dataset"
    assert report.quality.critical_gold_label_count == 0
    assert report.quality.critical_error_rate == 0.0


async def test_evaluation_reports_evidence_line_mismatch(
    gold_session: AsyncSession,
) -> None:
    _, version_id = await _prepare_corpus(gold_session)
    month = await _label_by_name(gold_session, "月")
    gold_session.add(
        _assignment(
            version_id=version_id,
            label_id=month.id,
            generation_method="manual",
            origin_ref="manual:gold:month",
            evidence_text="床前明月光",
            line_start=1,
            line_end=1,
        )
    )
    await gold_session.commit()
    dataset = _gold_dataset(
        labels=[
            _gold_label(
                dimension="imagery",
                name="月",
                evidence_text=POEM_CONTENT,
                line_start=2,
                line_end=2,
            )
        ]
    )

    report = await DomainLabelGoldEvaluator(gold_session).evaluate(dataset)

    assert report.quality.true_positive == 1
    assert report.quality.evidence_error_count == 1
    assert report.quality.evidence_error_rate == 1.0
    issue = report.records[0].evidence_issues[0]
    assert issue.canonical_name == "月"
    assert issue.observed_line_start == 1
    assert issue.reason == "已批准标签行号未命中任何金标准证据范围"


async def test_evaluation_prefers_manual_evidence_over_lower_priority_sources(
    gold_session: AsyncSession,
) -> None:
    _, version_id = await _prepare_corpus(gold_session)
    month = await _label_by_name(gold_session, "月")
    merged = DomainLabel(
        dimension=month.dimension,
        canonical_name="皓月",
        normalized_name="皓月",
        status=DomainLabelStatus.MERGED.value,
        merged_into_id=month.id,
    )
    gold_session.add(merged)
    await gold_session.flush()
    gold_session.add_all(
        [
            _assignment(
                version_id=version_id,
                label_id=month.id,
                generation_method="manual",
                origin_ref="manual:gold:month",
                evidence_text=POEM_CONTENT,
                line_start=1,
                line_end=2,
            ),
            _assignment(
                version_id=version_id,
                label_id=merged.id,
                generation_method="ai",
                origin_ref="ai:gold:merged-month",
                evidence_text=None,
                line_start=None,
                line_end=None,
                model_name="test-model",
                task_version="test-task",
            ),
            _assignment(
                version_id=version_id,
                label_id=month.id,
                generation_method="public_dataset",
                origin_ref="public_dataset:gold:month",
                evidence_text=None,
                line_start=None,
                line_end=None,
            ),
        ]
    )
    await gold_session.commit()
    dataset = _gold_dataset(
        labels=[
            _gold_label(
                dimension="imagery",
                name="月",
                evidence_text=POEM_CONTENT,
                line_start=1,
                line_end=2,
            )
        ]
    )

    report = await DomainLabelGoldEvaluator(gold_session).evaluate(dataset)

    assert report.metrics.micro.predicted_count == 1
    assert report.metrics.micro.true_positive == 1
    assert report.quality.approved_assignment_count == 3
    assert report.quality.evidence_error_count == 0
    assert report.records[0].extra_labels == []


async def test_evaluation_fails_when_source_content_hash_changed(
    gold_session: AsyncSession,
) -> None:
    await _prepare_corpus(gold_session)

    with pytest.raises(ValueError, match="内容哈希不一致"):
        await DomainLabelGoldEvaluator(gold_session).evaluate(
            _gold_dataset(expected_hash="0" * 64)
        )


async def test_evaluation_fails_when_gold_evidence_exceeds_version_lines(
    gold_session: AsyncSession,
) -> None:
    _, version_id = await _prepare_corpus(gold_session)
    await _approve_matching_labels(gold_session, version_id)
    dataset = _gold_dataset(
        labels=[
            _gold_label(
                dimension="imagery",
                name="月",
                evidence_text=POEM_CONTENT,
                line_start=1,
                line_end=9,
            )
        ]
    )

    with pytest.raises(ValueError, match="超出当前版本正文范围"):
        await DomainLabelGoldEvaluator(gold_session).evaluate(dataset)


def test_frozen_gold_dataset_is_valid() -> None:
    payload = json.loads(GOLD_DATASET_PATH.read_text(encoding="utf-8"))

    dataset = DomainLabelGoldDataset.model_validate(payload)

    assert dataset.version == "domain-label-gold-v1"
    assert len(dataset.records) == 5
    assert sum(len(record.labels) for record in dataset.records) == 14
    assert (
        sum(
            label.severity.value == "critical"
            for record in dataset.records
            for label in record.labels
        )
        == 8
    )
    assert dataset.target_record_counts["allusion"] == 4


def test_frozen_gold_hashes_match_converted_corpus(
    converted_corpus_records: list[dict[str, object]],
) -> None:
    payload = json.loads(GOLD_DATASET_PATH.read_text(encoding="utf-8"))
    dataset = DomainLabelGoldDataset.model_validate(payload)
    hashes = {
        str(record["external_id"]): sha256_text(str(record["content"]))
        for record in converted_corpus_records
    }

    for record in dataset.records:
        assert record.external_id in hashes, record.external_id
        assert hashes[record.external_id] == record.expected_source_content_hash
