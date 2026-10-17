from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from app.evaluation.domain_label_gold_freeze import (
    CRITICAL_IMAGERY_PATTERNS,
    CRITICAL_THEME_PATTERNS,
    DomainLabelGoldFreezer,
    audit_critical_label,
    resolve_critical_severity,
)
from app.evaluation.domain_label_review import (
    DomainLabelGoldV2ReviewWorkspace,
)
from app.models.domain_label import DomainLabelDimension
from app.schemas.domain_label_annotation import (
    DomainLabelAnnotationDraft,
)
from app.schemas.domain_label_gold import (
    DomainLabelGoldEvidence,
    DomainLabelGoldLabel,
    DomainLabelGoldSeverity,
)
from app.schemas.domain_label_review import DomainLabelGoldV2Review
from app.schemas.domain_label_sampling import (
    DomainLabelSamplingManifest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"
)
ANNOTATION_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_annotation.json"
)
REVIEW_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_review.json"
)
SCRIPT_PATH = (
    PROJECT_ROOT
    / "apps"
    / "api"
    / "scripts"
    / "freeze_domain_label_gold.py"
)

AUDIT_ISSUE_EXTERNAL_ID = "5b9a147f367d5cab1866880f"


def _ready_payload() -> dict[str, Any]:
    workspace = DomainLabelGoldV2ReviewWorkspace.from_path(
        manifest_path=MANIFEST_PATH,
        annotation_path=ANNOTATION_PATH,
    )
    payload = workspace.create_review().model_dump(mode="json")
    for item in payload["label_reviews"]:
        item["decision"] = "approve"
    for item in payload["empty_record_reviews"]:
        item["decision"] = "approve"
    payload["status"] = "ready_to_freeze"
    return payload


def _synthetic_freezer() -> DomainLabelGoldFreezer:
    workspace = DomainLabelGoldV2ReviewWorkspace.from_path(
        manifest_path=MANIFEST_PATH,
        annotation_path=ANNOTATION_PATH,
    )
    return DomainLabelGoldFreezer(
        manifest=workspace.manifest,
        annotation=workspace.annotation,
        review=DomainLabelGoldV2Review.model_validate(_ready_payload()),
        manifest_sha256=workspace.manifest_sha256,
        annotation_sha256=workspace.annotation_sha256,
    )


def _real_freezer() -> DomainLabelGoldFreezer:
    return DomainLabelGoldFreezer.from_path(
        manifest_path=MANIFEST_PATH,
        annotation_path=ANNOTATION_PATH,
        review_path=REVIEW_PATH,
    )


@pytest.mark.parametrize(
    ("dimension", "name", "expected"),
    [
        (DomainLabelDimension.IMAGERY, "月", DomainLabelGoldSeverity.CRITICAL),
        (DomainLabelDimension.IMAGERY, "杨柳", DomainLabelGoldSeverity.CRITICAL),
        (DomainLabelDimension.IMAGERY, "月亮", DomainLabelGoldSeverity.NORMAL),
        (DomainLabelDimension.EMOTION, "孤独", DomainLabelGoldSeverity.NORMAL),
        (DomainLabelDimension.THEME, "边塞", DomainLabelGoldSeverity.CRITICAL),
        (DomainLabelDimension.THEME, "中秋", DomainLabelGoldSeverity.CRITICAL),
        (DomainLabelDimension.ALLUSION, "庄周梦蝶", DomainLabelGoldSeverity.NORMAL),
    ],
)
def test_severity_follows_rule_table(
    dimension: DomainLabelDimension,
    name: str,
    expected: DomainLabelGoldSeverity,
) -> None:
    assert resolve_critical_severity(dimension, name) is expected


def test_critical_imagery_patterns_exclude_non_imagery_spans() -> None:
    assert CRITICAL_IMAGERY_PATTERNS["月"].matches("岁月催人老") is False
    assert CRITICAL_IMAGERY_PATTERNS["月"].matches("床前明月光") is True
    assert CRITICAL_IMAGERY_PATTERNS["月"].matches("六月十五团圆夜") is True
    assert CRITICAL_IMAGERY_PATTERNS["鸿雁"].matches("雁门关外野人家") is False
    assert CRITICAL_IMAGERY_PATTERNS["鸿雁"].matches("归雁洛阳边") is True
    assert CRITICAL_IMAGERY_PATTERNS["杨柳"].matches("风细柳斜斜") is True


def test_critical_theme_patterns_require_strong_forms() -> None:
    assert CRITICAL_THEME_PATTERNS["登临"].matches("欲穷千里目，更上一层楼") is True
    assert CRITICAL_THEME_PATTERNS["登临"].matches("举头望明月") is False
    assert CRITICAL_THEME_PATTERNS["山水田园"].matches("空山新雨后") is True
    assert CRITICAL_THEME_PATTERNS["羁旅"].matches("万里悲秋常作客") is True
    assert (
        CRITICAL_THEME_PATTERNS["羁旅"].matches(
            "桃李春风一杯酒，江湖夜雨十年灯。"
        )
        is False
    )


def test_freeze_refuses_when_review_is_not_ready() -> None:
    workspace = DomainLabelGoldV2ReviewWorkspace.from_path(
        manifest_path=MANIFEST_PATH,
        annotation_path=ANNOTATION_PATH,
    )
    freezer = DomainLabelGoldFreezer(
        manifest=workspace.manifest,
        annotation=workspace.annotation,
        review=workspace.create_review(),
        manifest_sha256=workspace.manifest_sha256,
        annotation_sha256=workspace.annotation_sha256,
    )

    with pytest.raises(ValueError, match="ready_to_freeze"):
        freezer.freeze()


def test_all_approve_freeze_keeps_empty_records_empty() -> None:
    dataset, summary = _synthetic_freezer().freeze()

    assert summary.label_count == 41
    assert summary.empty_record_count == 6
    assert sum(1 for record in dataset.records if not record.labels) == 6


def test_frozen_gold_dataset_locks_reviewed_labels() -> None:
    dataset, summary = _real_freezer().freeze()

    assert dataset.version == "domain-label-gold-v2"
    assert summary.review_version == "domain-label-gold-v2-independent-review"
    assert summary.record_count == 24
    assert summary.label_count == 45
    assert summary.critical_label_count == 18
    assert summary.empty_record_count == 4
    assert summary.dimension_label_counts == {
        "imagery": 10,
        "emotion": 27,
        "theme": 8,
        "allusion": 0,
    }
    assert summary.dimension_record_counts == {
        "imagery": 10,
        "emotion": 16,
        "theme": 8,
        "allusion": 0,
    }


def test_frozen_label_severity_equals_rule_table_assignment() -> None:
    dataset, _ = _real_freezer().freeze()

    overridden = [
        (record.external_id, label.dimension.value, label.name)
        for record in dataset.records
        for label in record.labels
        if label.severity
        != resolve_critical_severity(label.dimension, label.name)
    ]
    assert overridden == []

    upgraded = [
        label
        for record in dataset.records
        for label in record.labels
        if label.severity is DomainLabelGoldSeverity.CRITICAL
    ]
    assert len(upgraded) == 18


def test_frozen_records_carry_manifest_hash_and_composed_notes() -> None:
    dataset, _ = _real_freezer().freeze()
    manifest = DomainLabelSamplingManifest.model_validate_json(
        MANIFEST_PATH.read_bytes()
    )
    manifest_by_id = {
        record.external_id: record for record in manifest.records
    }
    annotation = DomainLabelAnnotationDraft.model_validate_json(
        ANNOTATION_PATH.read_bytes()
    )
    notes_by_id = {
        record.external_id: record.review_note for record in dataset.records
    }

    for record in dataset.records:
        source = manifest_by_id[record.external_id]
        assert record.expected_source_content_hash == source.content_hash
        assert record.title == source.title
        assert record.author == source.author
    for annotation_record in annotation.records:
        if annotation_record.review_note:
            note = notes_by_id[annotation_record.external_id]
            assert note is not None
            assert "盲标备注：" in note
    for external_id in (
        "5b9a186a367d5c0f40d22e25",
        "5b9a1430367d5cab18668600",
    ):
        note = notes_by_id[external_id]
        assert note is not None
        assert "复核备注：" in note


def test_critical_audit_surfaces_evidence_without_strong_trigger() -> None:
    _, summary = _real_freezer().freeze()

    assert summary.audit_issue_count == 1
    issue = summary.audit_issues[0]
    assert issue.external_id == AUDIT_ISSUE_EXTERNAL_ID
    assert issue.dimension == "theme"
    assert issue.name == "羁旅"
    assert "桃李春风" in issue.evidence_text


def test_audit_critical_label_checks_evidence_against_rule_table() -> None:
    missing_trigger = DomainLabelGoldLabel(
        dimension=DomainLabelDimension.THEME,
        name="羁旅",
        severity=DomainLabelGoldSeverity.CRITICAL,
        note=None,
        evidence=[
            DomainLabelGoldEvidence(
                evidence_text="桃李春风一杯酒，江湖夜雨十年灯。",
                line_start=1,
                line_end=2,
            )
        ],
    )
    with_trigger = missing_trigger.model_copy(
        update={
            "evidence": [
                DomainLabelGoldEvidence(
                    evidence_text="万里悲秋常作客",
                    line_start=1,
                    line_end=1,
                )
            ]
        }
    )

    assert audit_critical_label(missing_trigger) is not None
    assert audit_critical_label(with_trigger) is None


def test_freeze_output_is_deterministic() -> None:
    first_dataset, _ = _real_freezer().freeze()
    second_dataset, _ = _real_freezer().freeze()

    assert first_dataset.model_dump_json() == second_dataset.model_dump_json()


def test_cli_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "domain_label_gold_v2.json"
    output.write_text("existing", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--output", str(output)],
        capture_output=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 2
    assert "拒绝覆盖" in result.stderr
    assert output.read_text(encoding="utf-8") == "existing"
