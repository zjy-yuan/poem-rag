from __future__ import annotations

from pathlib import Path

import pytest
from app.evaluation.domain_label_annotation import (
    DomainLabelAnnotationWorkspace,
)
from app.schemas.domain_label_annotation import (
    DomainLabelAnnotationDraft,
    DomainLabelAnnotationDraftStatus,
    DomainLabelAnnotationRecordStatus,
)
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"
)
ANNOTATION_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_annotation.json"
)


def _workspace() -> DomainLabelAnnotationWorkspace:
    return DomainLabelAnnotationWorkspace.from_path(MANIFEST_PATH)


def _ready_payload(
    draft: DomainLabelAnnotationDraft,
) -> dict[str, object]:
    payload = draft.model_dump(mode="json")
    for record in payload["records"]:
        record["annotation_status"] = DomainLabelAnnotationRecordStatus.REVIEWED.value
    payload["records"][2]["labels"] = [
        {
            "dimension": "imagery",
            "name": "月",
            "severity": "normal",
            "note": None,
            "evidence": [
                {
                    "evidence_text": "明月松间照",
                    "line_start": 2,
                    "line_end": 2,
                }
            ],
        }
    ]
    payload["status"] = DomainLabelAnnotationDraftStatus.READY_FOR_REVIEW.value
    return payload


def test_annotation_init_is_deterministic_and_label_blind() -> None:
    workspace = _workspace()

    first = workspace.create_draft()
    second = workspace.create_draft()

    assert first.model_dump() == second.model_dump()
    assert first.version == "domain-label-gold-v2-annotation"
    assert first.source_manifest_version == workspace.manifest.version
    assert first.source_manifest_sha256 == workspace.manifest_sha256
    assert first.source_manifest_generated_at == workspace.manifest.generated_at
    assert len(first.records) == 24
    assert [record.external_id for record in first.records] == [
        record.external_id for record in workspace.manifest.records
    ]
    assert all(
        record.annotation_status
        == DomainLabelAnnotationRecordStatus.PENDING
        for record in first.records
    )
    assert all(not record.labels for record in first.records)

    summary = workspace.validate(first)
    assert summary.record_count == 24
    assert summary.reviewed_record_count == 0
    assert summary.pending_record_count == 24
    assert summary.label_count == 0
    assert summary.critical_label_count == 0
    assert sum(summary.dimension_counts.values()) == 0


def test_frozen_annotation_workspace_contract() -> None:
    workspace = _workspace()
    draft = DomainLabelAnnotationDraft.model_validate_json(
        ANNOTATION_PATH.read_bytes()
    )

    summary = workspace.validate(draft)

    assert summary.record_count == 24
    assert draft.source_manifest_version == workspace.manifest.version
    assert draft.source_manifest_sha256 == workspace.manifest_sha256
    assert draft.source_manifest_generated_at == workspace.manifest.generated_at
    assert [record.external_id for record in draft.records] == [
        record.external_id for record in workspace.manifest.records
    ]
    assert summary.label_count == sum(len(record.labels) for record in draft.records)
    assert (
        summary.reviewed_record_count + summary.pending_record_count
        == summary.record_count
    )


def test_annotation_rejects_manifest_hash_mismatch() -> None:
    workspace = _workspace()
    draft = workspace.create_draft()
    stale_workspace = DomainLabelAnnotationWorkspace(
        manifest=workspace.manifest,
        manifest_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="SHA-256"):
        stale_workspace.validate(draft)


def test_annotation_requires_exact_manifest_record_coverage() -> None:
    workspace = _workspace()
    payload = workspace.create_draft().model_dump(mode="json")
    payload["records"][0]["external_id"] = "extra-external-id"
    draft = DomainLabelAnnotationDraft.model_validate(payload)

    with pytest.raises(ValueError, match="覆盖且只能覆盖"):
        workspace.validate(draft)


def test_annotation_ready_status_requires_all_records_reviewed() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace.create_draft())
    payload["records"][0]["annotation_status"] = (
        DomainLabelAnnotationRecordStatus.PENDING.value
    )

    with pytest.raises(ValidationError, match="不能存在 pending 记录"):
        DomainLabelAnnotationDraft.model_validate(payload)


def test_annotation_ready_status_requires_at_least_one_label() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace.create_draft())
    payload["records"][2]["labels"] = []

    with pytest.raises(ValidationError, match="至少需要一条标签"):
        DomainLabelAnnotationDraft.model_validate(payload)


def test_annotation_accepts_reviewed_record_with_no_labels() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace.create_draft())
    payload["records"][0]["annotation_status"] = (
        DomainLabelAnnotationRecordStatus.REVIEWED.value
    )
    payload["records"][0]["labels"] = []
    draft = DomainLabelAnnotationDraft.model_validate(payload)

    summary = workspace.validate(draft)

    assert summary.reviewed_record_count == 24
    assert summary.pending_record_count == 0
    assert summary.label_count == 1


def test_annotation_rejects_unknown_controlled_label() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace.create_draft())
    payload["records"][2]["labels"][0]["name"] = "模型猜测意象"
    draft = DomainLabelAnnotationDraft.model_validate(payload)

    with pytest.raises(ValueError, match="非受控标签"):
        workspace.validate(draft)


def test_annotation_rejects_evidence_outside_frozen_content() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace.create_draft())
    payload["records"][2]["labels"][0]["evidence"][0]["line_end"] = 999
    draft = DomainLabelAnnotationDraft.model_validate(payload)

    with pytest.raises(ValueError, match="超出冻结正文范围"):
        workspace.validate(draft)


def test_annotation_rejects_duplicate_labels() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace.create_draft())
    duplicate = payload["records"][2]["labels"][0]
    payload["records"][2]["labels"].append(duplicate)

    with pytest.raises(ValidationError, match="不能重复标签"):
        DomainLabelAnnotationDraft.model_validate(payload)


def test_review_markdown_contains_line_numbers_without_prediction_leakage() -> None:
    markdown = _workspace().export_review_markdown()

    assert "# 领域标签独立人工盲标审阅稿" in markdown
    assert "### 行号正文" in markdown
    assert "1 |" in markdown
    assert "2 | \n" not in markdown
    assert "`5b9a04a1367d5cae5c9bff58`" in markdown
    assert "manual_labels" not in markdown
    assert "predicted" not in markdown
    assert "prediction" not in markdown
    assert '"labels"' not in markdown
