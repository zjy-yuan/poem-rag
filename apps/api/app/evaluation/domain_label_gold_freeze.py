from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.evaluation.domain_label_annotation import sha256_file
from app.evaluation.domain_label_review import (
    DomainLabelGoldV2ReviewWorkspace,
    build_final_label_map,
)
from app.models.domain_label import DomainLabelDimension
from app.schemas.domain_label_annotation import (
    DomainLabelAnnotationDraft,
    DomainLabelAnnotationRecord,
)
from app.schemas.domain_label_gold import (
    DomainLabelGoldDataset,
    DomainLabelGoldLabel,
    DomainLabelGoldRecord,
    DomainLabelGoldSeverity,
    DomainLabelGoldSource,
)
from app.schemas.domain_label_gold_freeze import (
    DomainLabelCriticalAuditIssue,
    DomainLabelGoldFreezeSummary,
)
from app.schemas.domain_label_review import DomainLabelGoldV2Review
from app.schemas.domain_label_sampling import (
    DomainLabelSamplingManifest,
)

GOLD_V2_DATASET_VERSION = "domain-label-gold-v2"
GOLD_V2_DATASET_NAME = "领域标签人工金标准 v2（24 首独立泛化样本）"
GOLD_V2_DATASET_DESCRIPTION = (
    "冻结 24 首独立人工盲标作品经独立复核后的最终标签，作为在线已审核标签的泛化评估金标准。"
    "标签与证据来自人工标注和独立复核，severity 由 critical 规则表按标签名确定性赋值，"
    "正文哈希沿用冻结采样清单。"
)
GOLD_V2_SELECTION_STRATEGY = (
    "沿用 v1 同源分层随机采样，seed=domain-label-gold-v2，排除 v1 已冻结的 5 首；"
    "24 首全部完成人工盲标与独立复核，target_record_counts 沿用项目级金标准覆盖计划"
    "以保持跨版本可比。"
)
GOLD_V2_SOURCE = DomainLabelGoldSource(
    source_key="project-manual-gold-standard",
    source_name="项目人工金标准样本",
    source_url=None,
    license_note=(
        "项目内人工确认标签，仅用于离线评估标签器与在线标签视图的一致性；"
        "不得作为公开数据集分发。"
    ),
)
GOLD_V2_POEM_SOURCE_KEY = "aopao-chinese-gushiwen"
GOLD_V2_TARGET_RECORD_COUNTS = {
    DomainLabelDimension.IMAGERY: 12,
    DomainLabelDimension.EMOTION: 12,
    DomainLabelDimension.THEME: 8,
    DomainLabelDimension.ALLUSION: 4,
}


@dataclass(frozen=True)
class DomainLabelCriticalPattern:
    """Surface forms that can deterministically support one critical label.

    `include` is the strong-form regex, `exclude` lists spans stripped before
    matching so month names, place names and idioms do not look like imagery.
    The pattern is an audit tool, never a freeze gate.
    """

    include: str
    exclude: str | None = None

    def matches(self, text: str) -> bool:
        candidate = re.sub(self.exclude, "", text) if self.exclude else text
        return bool(re.search(self.include, candidate))


CRITICAL_IMAGERY_PATTERNS: dict[str, DomainLabelCriticalPattern] = {
    "月": DomainLabelCriticalPattern(
        include=r"月",
        exclude=r"(正|一|二|三|四|五|六|七|八|九|十|冬|腊)月(?!十五)|岁月|月余",
    ),
    "杨柳": DomainLabelCriticalPattern(
        include=r"杨柳|垂杨|绿柳|杨花|柳[绵絮丝色岸叶荫影花条带枝梢眼腰陌径堤斜]",
    ),
    "流水": DomainLabelCriticalPattern(
        include=r"流水|逝水|江水|江流|溪流|水流|川流|春水|秋水|东流",
    ),
    "落花": DomainLabelCriticalPattern(
        include=r"落花|花落|飞花|乱红|残红|落英|花谢|花飞|花褪|花残|落红",
    ),
    "夕阳": DomainLabelCriticalPattern(
        include=r"夕阳|落日|残阳|斜阳|白日|夕照|日暮|暮日|晚日|暮云",
    ),
    "鸿雁": DomainLabelCriticalPattern(
        include=r"雁",
        exclude=r"雁门|雁荡|雁塔",
    ),
}

CRITICAL_THEME_PATTERNS: dict[str, DomainLabelCriticalPattern] = {
    "边塞": DomainLabelCriticalPattern(
        include=(
            r"边塞|边关|关塞|塞上|塞下|塞外|塞垣|玉门|阳关|轮台|萧关|楼兰|凉州"
            r"|敦煌|燕然|狼山|居延|祁连|阴山|瀚海|大漠|长城|烽火|胡笳|羌笛"
            r"|从军|戍鼓|征人|铁衣|都护|单于|可汗"
        ),
    ),
    "山水田园": DomainLabelCriticalPattern(
        include=(
            r"田园|山居|山水|溪涧|桑麻|柴门|东篱|篱落|渔舟|渔父|渔翁|浣女"
            r"|林泉|空山|烟霞|辋川|终南|桃源|桃花源|稻田|桑野"
        ),
    ),
    "登临": DomainLabelCriticalPattern(
        include=(
            r"登[^，。！？；、\s]{0,6}(?:高|楼|台|阁|亭|陵|山|原|陇|岘|岵|临|眺)"
            r"|上[^，。！？；、\s]{0,6}(?:台|楼|阁|亭)"
            r"|凭栏|凭槛|倚栏"
        ),
    ),
    "羁旅": DomainLabelCriticalPattern(
        include=(
            r"羁旅|异乡|客居|客舍|游子|孤客|倦客|作客|为客|漂泊|飘泊"
            r"|行役|远游|万里|天涯"
        ),
    ),
    "中秋": DomainLabelCriticalPattern(
        include=r"中秋|八月十五|仲秋|三五夜",
    ),
}

CRITICAL_PATTERNS: dict[tuple[str, str], DomainLabelCriticalPattern] = {
    **{
        (DomainLabelDimension.IMAGERY.value, name): pattern
        for name, pattern in CRITICAL_IMAGERY_PATTERNS.items()
    },
    **{
        (DomainLabelDimension.THEME.value, name): pattern
        for name, pattern in CRITICAL_THEME_PATTERNS.items()
    },
}


def resolve_critical_severity(
    dimension: DomainLabelDimension,
    name: str,
) -> DomainLabelGoldSeverity:
    """Assign severity by rule table membership, never by per-record judgment."""

    if (dimension.value, name) not in CRITICAL_PATTERNS:
        return DomainLabelGoldSeverity.NORMAL
    return DomainLabelGoldSeverity.CRITICAL


def audit_critical_label(
    label: DomainLabelGoldLabel,
) -> str | None:
    """Audit one critical label; returns None when evidence carries a trigger."""

    pattern = CRITICAL_PATTERNS.get(label.lookup_key)
    if pattern is None:
        return None
    evidence_text = "\n".join(
        evidence.evidence_text for evidence in label.evidence
    )
    if pattern.matches(evidence_text):
        return None
    return "证据文本未命中 critical 规则表强触发词"


class DomainLabelGoldFreezer:
    """Freeze reviewed domain-label annotations into a gold dataset."""

    def __init__(
        self,
        *,
        manifest: DomainLabelSamplingManifest,
        annotation: DomainLabelAnnotationDraft,
        review: DomainLabelGoldV2Review,
        manifest_sha256: str,
        annotation_sha256: str,
    ) -> None:
        self.workspace = DomainLabelGoldV2ReviewWorkspace(
            manifest=manifest,
            annotation=annotation,
            manifest_sha256=manifest_sha256,
            annotation_sha256=annotation_sha256,
        )
        self.review = review

    @classmethod
    def from_path(
        cls,
        *,
        manifest_path: Path,
        annotation_path: Path,
        review_path: Path,
    ) -> DomainLabelGoldFreezer:
        manifest = DomainLabelSamplingManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        annotation = DomainLabelAnnotationDraft.model_validate_json(
            annotation_path.read_bytes()
        )
        review = DomainLabelGoldV2Review.model_validate_json(
            review_path.read_bytes()
        )
        return cls(
            manifest=manifest,
            annotation=annotation,
            review=review,
            manifest_sha256=sha256_file(manifest_path),
            annotation_sha256=sha256_file(annotation_path),
        )

    def freeze(
        self,
    ) -> tuple[DomainLabelGoldDataset, DomainLabelGoldFreezeSummary]:
        review_summary = self.workspace.validate(self.review)
        if not review_summary.ready_to_freeze:
            raise ValueError(
                "独立复核尚未 ready_to_freeze，拒绝冻结金标准: "
                f"pending={review_summary.pending_decision_count} "
                f"blocking={len(review_summary.blocking_review_ids)}"
            )
        manifest_by_id = {
            record.external_id: record
            for record in self.workspace.manifest.records
        }
        annotation_by_id = {
            record.external_id: record
            for record in self.workspace.annotation.records
        }
        final_labels = build_final_label_map(
            self.review,
            record_by_id=annotation_by_id,
        )
        review_notes_by_id = self._review_notes_by_record()

        records: list[DomainLabelGoldRecord] = []
        audit_issues: list[DomainLabelCriticalAuditIssue] = []
        label_counts: Counter[str] = Counter()
        record_counts: Counter[str] = Counter()
        critical_label_count = 0
        for annotation_record in self.workspace.annotation.records:
            manifest_record = manifest_by_id[annotation_record.external_id]
            labels: list[DomainLabelGoldLabel] = []
            seen_dimensions: set[str] = set()
            for label in final_labels[annotation_record.external_id]:
                frozen = self._freeze_label(label)
                label_counts[frozen.dimension.value] += 1
                if frozen.dimension.value not in seen_dimensions:
                    seen_dimensions.add(frozen.dimension.value)
                    record_counts[frozen.dimension.value] += 1
                audit_reason = audit_critical_label(frozen)
                if audit_reason is not None:
                    audit_issues.append(
                        DomainLabelCriticalAuditIssue(
                            external_id=manifest_record.external_id,
                            dimension=frozen.dimension.value,
                            name=frozen.name,
                            evidence_text="\n".join(
                                evidence.evidence_text
                                for evidence in frozen.evidence
                            ),
                            reason=audit_reason,
                        )
                    )
                if frozen.severity == DomainLabelGoldSeverity.CRITICAL:
                    critical_label_count += 1
                labels.append(frozen)
            records.append(
                DomainLabelGoldRecord(
                    external_id=manifest_record.external_id,
                    title=manifest_record.title,
                    author=manifest_record.author,
                    expected_source_content_hash=manifest_record.content_hash,
                    review_note=self._record_note(
                        annotation_record,
                        review_notes_by_id,
                    ),
                    labels=labels,
                )
            )

        dataset = DomainLabelGoldDataset(
            version=GOLD_V2_DATASET_VERSION,
            name=GOLD_V2_DATASET_NAME,
            description=GOLD_V2_DATASET_DESCRIPTION,
            selection_strategy=GOLD_V2_SELECTION_STRATEGY,
            source=GOLD_V2_SOURCE,
            poem_source_key=GOLD_V2_POEM_SOURCE_KEY,
            target_record_counts=GOLD_V2_TARGET_RECORD_COUNTS,
            records=records,
        )
        summary = DomainLabelGoldFreezeSummary(
            version=GOLD_V2_DATASET_VERSION,
            review_version=self.review.version,
            record_count=len(records),
            label_count=sum(label_counts.values()),
            critical_label_count=critical_label_count,
            empty_record_count=sum(not record.labels for record in records),
            dimension_label_counts={
                dimension.value: label_counts[dimension.value]
                for dimension in DomainLabelDimension
            },
            dimension_record_counts={
                dimension.value: record_counts[dimension.value]
                for dimension in DomainLabelDimension
            },
            audit_issue_count=len(audit_issues),
            audit_issues=audit_issues,
        )
        return dataset, summary

    def _review_notes_by_record(self) -> dict[str, list[str]]:
        notes: dict[str, list[str]] = {}
        for item in [*self.review.label_reviews, *self.review.empty_record_reviews]:
            if not item.review_note:
                continue
            notes.setdefault(item.external_id, []).append(item.review_note)
        return notes

    @staticmethod
    def _record_note(
        annotation_record: DomainLabelAnnotationRecord,
        review_notes_by_id: Mapping[str, list[str]],
    ) -> str | None:
        parts: list[str] = []
        if annotation_record.review_note:
            parts.append(f"盲标备注：{annotation_record.review_note}")
        parts.extend(
            f"复核备注：{note}"
            for note in review_notes_by_id.get(annotation_record.external_id, [])
        )
        if not parts:
            return None
        note = "；".join(parts)
        if len(note) > 1000:
            raise ValueError(
                "金标准作品备注超过 1000 字，需要人工精简: "
                f"external_id={annotation_record.external_id} length={len(note)}"
            )
        return note

    @staticmethod
    def _freeze_label(label: DomainLabelGoldLabel) -> DomainLabelGoldLabel:
        severity = resolve_critical_severity(label.dimension, label.name)
        if label.severity == severity:
            return label
        return label.model_copy(update={"severity": severity})


def format_domain_label_gold_freeze_summary(
    summary: DomainLabelGoldFreezeSummary,
) -> str:
    lines = [
        (
            f"version={summary.version} review={summary.review_version} "
            f"records={summary.record_count} labels={summary.label_count} "
            f"critical={summary.critical_label_count} "
            f"empty_records={summary.empty_record_count}"
        ),
        (
            "dimension_labels "
            + " ".join(
                f"{dimension}={count}"
                for dimension, count in summary.dimension_label_counts.items()
            )
        ),
        (
            "dimension_records "
            + " ".join(
                f"{dimension}={count}"
                for dimension, count in summary.dimension_record_counts.items()
            )
        ),
        f"critical_audit_issues={summary.audit_issue_count}",
    ]
    if summary.audit_issues:
        lines.append("critical_audit_issue_details:")
        lines.extend(
            f"- {issue.external_id} | {issue.dimension}:{issue.name} | {issue.reason}"
            for issue in summary.audit_issues
        )
    return "\n".join(lines)
