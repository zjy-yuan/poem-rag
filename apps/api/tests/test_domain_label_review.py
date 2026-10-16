from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from app.evaluation.domain_label_review import (
    DomainLabelGoldV2ReviewWorkspace,
)
from app.schemas.domain_label_review import (
    DomainLabelGoldV2Review,
)
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"
)
ANNOTATION_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_annotation.json"
)


def _workspace() -> DomainLabelGoldV2ReviewWorkspace:
    return DomainLabelGoldV2ReviewWorkspace.from_path(
        manifest_path=MANIFEST_PATH,
        annotation_path=ANNOTATION_PATH,
    )


def _review(
    workspace: DomainLabelGoldV2ReviewWorkspace,
) -> DomainLabelGoldV2Review:
    return workspace.create_review()


def _payload(
    workspace: DomainLabelGoldV2ReviewWorkspace,
) -> dict[str, Any]:
    return _review(workspace).model_dump(mode="json")


def _ready_payload(
    workspace: DomainLabelGoldV2ReviewWorkspace,
) -> dict[str, Any]:
    payload = _payload(workspace)
    for item in payload["label_reviews"]:
        item["decision"] = "approve"
    for item in payload["empty_record_reviews"]:
        item["decision"] = "approve"
    payload["status"] = "ready_to_freeze"
    return payload


def _label_review(
    payload: dict[str, Any],
    *,
    external_id: str,
    name: str,
) -> dict[str, Any]:
    return next(
        item
        for item in payload["label_reviews"]
        if item["external_id"] == external_id
        and item["original_label"]["name"] == name
    )


def _empty_review(
    payload: dict[str, Any],
    *,
    external_id: str,
) -> dict[str, Any]:
    return next(
        item
        for item in payload["empty_record_reviews"]
        if item["external_id"] == external_id
    )


def test_review_init_covers_all_manual_labels_and_empty_conclusions() -> None:
    workspace = _workspace()

    first = _review(workspace)
    second = _review(workspace)

    assert first.model_dump() == second.model_dump()
    assert first.version == "domain-label-gold-v2-independent-review"
    assert first.source_annotation_version == workspace.annotation.version
    assert first.source_annotation_sha256 == workspace.annotation_sha256
    assert first.source_manifest_version == workspace.manifest.version
    assert first.source_manifest_sha256 == workspace.manifest_sha256
    assert len(first.label_reviews) == 41
    assert len(first.empty_record_reviews) == 6
    assert Counter(
        item.original_label.dimension.value for item in first.label_reviews
    ) == Counter(
        {
            "imagery": 9,
            "emotion": 24,
            "theme": 8,
            "allusion": 0,
        }
    )
    assert {
        item.external_id for item in first.empty_record_reviews
    } == {
        "5b9a120a367d5c5b84062a89",
        "5b9a0255367d5caccce1aa1c",
        "5b9a0f31367d5c3b48e948fa",
        "5b9a186a367d5c0f40d22e25",
        "5b9a19fb367d5c0f40d238ae",
        "5b9a1430367d5cab18668600",
    }
    assert all(item.decision.value == "pending" for item in first.label_reviews)
    assert all(
        item.decision.value == "pending"
        for item in first.empty_record_reviews
    )


def test_blank_review_is_blocked_from_freezing() -> None:
    workspace = _workspace()

    summary = workspace.validate(_review(workspace))

    assert summary.label_review_count == 41
    assert summary.empty_record_review_count == 6
    assert summary.decided_label_review_count == 0
    assert summary.decided_empty_record_review_count == 0
    assert summary.pending_decision_count == 47
    assert len(summary.blocking_review_ids) == 47
    assert summary.ready_to_freeze is False


def test_review_rejects_annotation_or_manifest_hash_mismatch() -> None:
    workspace = _workspace()
    annotation_payload = _payload(workspace)
    annotation_payload["source_annotation_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="标注文件 SHA-256"):
        workspace.validate(
            DomainLabelGoldV2Review.model_validate(annotation_payload)
        )

    manifest_payload = _payload(workspace)
    manifest_payload["source_manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="采样清单 SHA-256"):
        workspace.validate(
            DomainLabelGoldV2Review.model_validate(manifest_payload)
        )


def test_review_requires_exact_label_and_empty_record_coverage() -> None:
    workspace = _workspace()
    missing_payload = _payload(workspace)
    missing_payload["label_reviews"].pop()
    with pytest.raises(ValueError, match="覆盖且只能覆盖"):
        workspace.validate(
            DomainLabelGoldV2Review.model_validate(missing_payload)
        )

    extra_payload = _payload(workspace)
    extra = copy.deepcopy(extra_payload["label_reviews"][0])
    extra["review_id"] = "label:not-in-annotation"
    extra_payload["label_reviews"].append(extra)
    with pytest.raises(ValueError, match="覆盖且只能覆盖"):
        workspace.validate(
            DomainLabelGoldV2Review.model_validate(extra_payload)
        )

    empty_payload = _payload(workspace)
    empty_payload["empty_record_reviews"].pop()
    with pytest.raises(ValueError, match="覆盖且只能覆盖"):
        workspace.validate(
            DomainLabelGoldV2Review.model_validate(empty_payload)
        )


def test_review_rejects_modified_original_label() -> None:
    workspace = _workspace()
    payload = _payload(workspace)
    payload["label_reviews"][0]["original_label"]["name"] = "思乡"

    with pytest.raises(ValueError, match="不可变的 original_label"):
        workspace.validate(DomainLabelGoldV2Review.model_validate(payload))


def test_approved_label_with_mismatched_evidence_blocks_freezing() -> None:
    workspace = _workspace()
    workspace.annotation = workspace.annotation.model_copy(deep=True)
    label = workspace.annotation.records[0].labels[0]
    label.evidence[0].evidence_text = "正文中不存在的证据"
    payload = _ready_payload(workspace)
    review_id = payload["label_reviews"][0]["review_id"]

    summary = workspace.validate(DomainLabelGoldV2Review.model_validate(payload))

    assert summary.ready_to_freeze is False
    assert review_id in summary.blocking_review_ids


@pytest.mark.parametrize("decision", ["pending", "uncertain"])
def test_pending_or_uncertain_label_blocks_freezing(decision: str) -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace)
    payload["label_reviews"][0]["decision"] = decision

    summary = workspace.validate(DomainLabelGoldV2Review.model_validate(payload))

    assert summary.ready_to_freeze is False
    assert payload["label_reviews"][0]["review_id"] in summary.blocking_review_ids


def test_needs_change_label_requires_correction_and_valid_evidence() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace)
    item = _label_review(
        payload,
        external_id="5b9a1430367d5cab18668603",
        name="月",
    )
    item["decision"] = "needs_change"
    with pytest.raises(ValidationError, match="corrected_label"):
        DomainLabelGoldV2Review.model_validate(payload)

    item["corrected_label"] = {
        "dimension": "imagery",
        "name": "落花",
        "severity": "normal",
        "note": None,
        "evidence": [
            {
                "evidence_text": "山寺月中寻桂子，郡亭枕上看潮头。",
                "line_start": 3,
                "line_end": 3,
            }
        ],
    }
    summary = workspace.validate(DomainLabelGoldV2Review.model_validate(payload))

    assert summary.needs_change_label_count == 1
    assert summary.ready_to_freeze is True


def test_empty_record_reject_blocks_but_needs_change_can_add_labels() -> None:
    workspace = _workspace()
    rejected = _ready_payload(workspace)
    rejected_item = _empty_review(
        rejected,
        external_id="5b9a120a367d5c5b84062a89",
    )
    rejected_item["decision"] = "reject"
    rejected_summary = workspace.validate(
        DomainLabelGoldV2Review.model_validate(rejected)
    )

    assert rejected_summary.ready_to_freeze is False
    assert rejected_item["review_id"] in rejected_summary.blocking_review_ids

    changed = _ready_payload(workspace)
    changed_item = _empty_review(
        changed,
        external_id="5b9a120a367d5c5b84062a89",
    )
    changed_item["decision"] = "needs_change"
    changed_item["corrected_labels"] = [
        {
            "dimension": "theme",
            "name": "登临",
            "severity": "normal",
            "note": None,
            "evidence": [
                {
                    "evidence_text": "南朝四百八十寺，多少楼台烟雨中。",
                    "line_start": 2,
                    "line_end": 2,
                }
            ],
        }
    ]
    changed_summary = workspace.validate(
        DomainLabelGoldV2Review.model_validate(changed)
    )

    assert changed_summary.needs_change_empty_record_count == 1
    assert changed_summary.ready_to_freeze is True


def test_ready_to_freeze_requires_completed_status_and_decisions() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace)
    payload["status"] = "in_progress"

    summary = workspace.validate(DomainLabelGoldV2Review.model_validate(payload))

    assert summary.decided_label_review_count == 41
    assert summary.decided_empty_record_review_count == 6
    assert summary.ready_to_freeze is False


def test_review_cannot_remove_every_label() -> None:
    workspace = _workspace()
    payload = _ready_payload(workspace)
    for item in payload["label_reviews"]:
        item["decision"] = "reject"

    with pytest.raises(ValueError, match="至少需要保留一条标签"):
        workspace.validate(DomainLabelGoldV2Review.model_validate(payload))


def test_review_markdown_contains_all_records_and_review_items() -> None:
    workspace = _workspace()
    review = _review(workspace)

    markdown = workspace.export_review_markdown(review)

    assert "# 领域标签 v2 独立复核单" in markdown
    assert "只依据冻结正文判断" in markdown
    assert workspace.annotation_sha256 in markdown
    assert workspace.manifest_sha256 in markdown
    assert markdown.count("### `label:") == 41
    assert markdown.count("### `empty:") == 6
    for record in workspace.manifest.records:
        assert record.title in markdown
        assert record.external_id in markdown
    assert "predicted" not in markdown
    assert "manual_labels" not in markdown
