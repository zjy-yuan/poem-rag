from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from app.core.text import normalize_lookup
from app.evaluation.domain_label_annotation import (
    DomainLabelAnnotationWorkspace,
    sha256_file,
)
from app.schemas.domain_label_annotation import (
    DomainLabelAnnotationDraft,
    DomainLabelAnnotationDraftStatus,
    DomainLabelAnnotationRecord,
    DomainLabelAnnotationRecordStatus,
)
from app.schemas.domain_label_gold import DomainLabelGoldLabel
from app.schemas.domain_label_review import (
    DomainLabelEmptyRecordReview,
    DomainLabelGoldV2Review,
    DomainLabelLabelReview,
    DomainLabelReviewDecision,
    DomainLabelReviewStatus,
    DomainLabelReviewValidationSummary,
)
from app.schemas.domain_label_sampling import (
    DomainLabelSamplingManifest,
    DomainLabelSamplingRecord,
)

REVIEW_VERSION = "domain-label-gold-v2-independent-review"


class DomainLabelGoldV2ReviewWorkspace:
    """Create and validate an independent review of the v2 annotation draft."""

    def __init__(
        self,
        *,
        manifest: DomainLabelSamplingManifest,
        annotation: DomainLabelAnnotationDraft,
        manifest_sha256: str,
        annotation_sha256: str,
    ) -> None:
        self.manifest = manifest
        self.annotation = annotation
        self.manifest_sha256 = manifest_sha256
        self.annotation_sha256 = annotation_sha256
        self.annotation_workspace = DomainLabelAnnotationWorkspace(
            manifest,
            manifest_sha256,
        )

    @classmethod
    def from_path(
        cls,
        *,
        manifest_path: Path,
        annotation_path: Path,
    ) -> DomainLabelGoldV2ReviewWorkspace:
        manifest = DomainLabelSamplingManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        annotation = DomainLabelAnnotationDraft.model_validate_json(
            annotation_path.read_bytes()
        )
        return cls(
            manifest=manifest,
            annotation=annotation,
            manifest_sha256=sha256_file(manifest_path),
            annotation_sha256=sha256_file(annotation_path),
        )

    def create_review(self) -> DomainLabelGoldV2Review:
        self._validate_source_annotation()
        label_reviews: list[DomainLabelLabelReview] = []
        empty_record_reviews: list[DomainLabelEmptyRecordReview] = []
        for record in self.annotation.records:
            if record.labels:
                label_reviews.extend(
                    DomainLabelLabelReview(
                        review_id=self._label_review_id(
                            record.external_id,
                            label,
                        ),
                        external_id=record.external_id,
                        original_label=label,
                    )
                    for label in record.labels
                )
                continue
            empty_record_reviews.append(
                DomainLabelEmptyRecordReview(
                    review_id=self._empty_record_review_id(record.external_id),
                    external_id=record.external_id,
                )
            )

        return DomainLabelGoldV2Review(
            version=REVIEW_VERSION,
            source_annotation_version=self.annotation.version,
            source_annotation_sha256=self.annotation_sha256,
            source_manifest_version=self.annotation.source_manifest_version,
            source_manifest_sha256=self.annotation.source_manifest_sha256,
            label_reviews=label_reviews,
            empty_record_reviews=empty_record_reviews,
        )

    def validate(
        self,
        review: DomainLabelGoldV2Review,
    ) -> DomainLabelReviewValidationSummary:
        self._validate_source_annotation()
        self._validate_binding(review)
        annotation_by_id = {
            record.external_id: record for record in self.annotation.records
        }
        manifest_by_id = {
            record.external_id: record for record in self.manifest.records
        }
        expected_labels, expected_empty_ids = self._expected_review_keys()

        actual_label_reviews = {
            item.review_id: item for item in review.label_reviews
        }
        actual_empty_reviews = {
            item.review_id: item for item in review.empty_record_reviews
        }
        self._validate_exact_coverage(
            expected_labels=set(expected_labels),
            actual_labels=set(actual_label_reviews),
            expected_empty_ids=set(expected_empty_ids),
            actual_empty_ids={
                item.external_id for item in review.empty_record_reviews
            },
        )

        for review_id, (record, label) in expected_labels.items():
            item = actual_label_reviews[review_id]
            if item.external_id != record.external_id:
                raise ValueError(
                    "标签复核绑定到错误的作品: "
                    f"review_id={review_id} external_id={item.external_id}"
                )
            if item.original_label != label:
                raise ValueError(
                    "标签复核修改了不可变的 original_label: "
                    f"review_id={review_id}"
                )
        for external_id in expected_empty_ids:
            item = actual_empty_reviews[self._empty_record_review_id(external_id)]
            if item.external_id != external_id:
                raise ValueError(
                    "空标签复核绑定到错误的作品: "
                    f"review_id={item.review_id} "
                    f"external_id={item.external_id}"
                )

        final_labels = self._build_final_label_map(
            review,
            record_by_id=annotation_by_id,
            expected_labels=expected_labels,
            expected_empty_ids=expected_empty_ids,
        )
        self._validate_final_labels(final_labels)
        evidence_issues = self._preflight_label_reviews(
            review,
            record_by_id=manifest_by_id,
        )

        label_decision_counts = Counter(
            item.decision for item in review.label_reviews
        )
        empty_decision_counts = Counter(
            item.decision for item in review.empty_record_reviews
        )
        blocking_review_ids = self._blocking_review_ids(
            review,
            evidence_issue_ids=set(evidence_issues),
        )
        decided_label_review_count = sum(
            label_decision_counts[decision]
            for decision in (
                DomainLabelReviewDecision.APPROVE,
                DomainLabelReviewDecision.NEEDS_CHANGE,
                DomainLabelReviewDecision.REJECT,
            )
        )
        decided_empty_record_review_count = sum(
            empty_decision_counts[decision]
            for decision in (
                DomainLabelReviewDecision.APPROVE,
                DomainLabelReviewDecision.NEEDS_CHANGE,
            )
        )
        if (
            not any(final_labels.values())
            and decided_label_review_count == len(review.label_reviews)
            and decided_empty_record_review_count
            == len(review.empty_record_reviews)
        ):
            raise ValueError("独立复核后的金标准至少需要保留一条标签")
        ready_to_freeze = (
            review.status == DomainLabelReviewStatus.READY_TO_FREEZE
            and not blocking_review_ids
            and decided_label_review_count == len(review.label_reviews)
            and decided_empty_record_review_count
            == len(review.empty_record_reviews)
        )
        return DomainLabelReviewValidationSummary(
            version=review.version,
            status=review.status,
            label_review_count=len(review.label_reviews),
            empty_record_review_count=len(review.empty_record_reviews),
            decided_label_review_count=decided_label_review_count,
            decided_empty_record_review_count=decided_empty_record_review_count,
            pending_decision_count=(
                label_decision_counts[DomainLabelReviewDecision.PENDING]
                + empty_decision_counts[DomainLabelReviewDecision.PENDING]
            ),
            approve_label_count=label_decision_counts[
                DomainLabelReviewDecision.APPROVE
            ],
            needs_change_label_count=label_decision_counts[
                DomainLabelReviewDecision.NEEDS_CHANGE
            ],
            reject_label_count=label_decision_counts[
                DomainLabelReviewDecision.REJECT
            ],
            uncertain_label_count=label_decision_counts[
                DomainLabelReviewDecision.UNCERTAIN
            ],
            approve_empty_record_count=empty_decision_counts[
                DomainLabelReviewDecision.APPROVE
            ],
            needs_change_empty_record_count=empty_decision_counts[
                DomainLabelReviewDecision.NEEDS_CHANGE
            ],
            reject_empty_record_count=empty_decision_counts[
                DomainLabelReviewDecision.REJECT
            ],
            uncertain_empty_record_count=empty_decision_counts[
                DomainLabelReviewDecision.UNCERTAIN
            ],
            blocking_review_ids=blocking_review_ids,
            ready_to_freeze=ready_to_freeze,
        )

    def export_review_markdown(
        self,
        review: DomainLabelGoldV2Review,
    ) -> str:
        summary = self.validate(review)
        record_by_id = {
            record.external_id: record for record in self.manifest.records
        }
        annotation_by_id = {
            record.external_id: record for record in self.annotation.records
        }
        label_reviews_by_record: dict[str, list[DomainLabelLabelReview]] = {}
        for item in review.label_reviews:
            label_reviews_by_record.setdefault(item.external_id, []).append(item)
        empty_review_by_id = {
            item.external_id: item for item in review.empty_record_reviews
        }
        evidence_issue_ids = set(
            self._preflight_label_reviews(
                review,
                record_by_id=record_by_id,
            )
        )

        lines = [
            "# 领域标签 v2 独立复核单",
            "",
            f"> 复核版本：`{review.version}`",
            f"> 标注文件版本：`{review.source_annotation_version}`",
            f"> 标注文件 SHA-256：`{review.source_annotation_sha256}`",
            f"> 采样清单版本：`{review.source_manifest_version}`",
            f"> 采样清单 SHA-256：`{review.source_manifest_sha256}`",
            f"> 当前状态：`{review.status.value}`",
            (
                "> 待复核："
                f"{summary.label_review_count} 条标签、"
                f"{summary.empty_record_review_count} 个空标签结论"
            ),
            "",
            "复核时只依据冻结正文判断，不引入作者生平、编年、创作背景、模型预测或",
            "在线审核结果。逐条在 JSON 中填写 `decision`：",
            "",
            "- `approve`：原标注成立，保持不变。",
            "- `needs_change`：原标注方向成立，但名称、严重级别或证据边界需要修正；",
            "  必须填写 `corrected_label`，空标签结论则填写 `corrected_labels`。",
            "- `reject`：该标签不应进入金标准；对空标签结论使用 `reject` 会阻塞冻结，",
            "  应改用 `needs_change` 并给出应补充的标签。",
            "- `uncertain`：当前证据不足以确认，该状态会阻塞冻结。",
            "",
            "只有全部复核完成、没有 `pending / uncertain`、所有 `approve` 的证据通过",
            "自动预审，才可把复核状态改为 `ready_to_freeze`。",
        ]

        for index, annotation_record in enumerate(
            self.annotation.records,
            start=1,
        ):
            manifest_record = record_by_id[annotation_record.external_id]
            source_record = annotation_by_id[annotation_record.external_id]
            lines.extend(
                [
                    "",
                    f"## {index}. {manifest_record.title}",
                    "",
                    f"- 作者：{manifest_record.author}",
                    f"- 朝代：{manifest_record.dynasty}",
                    f"- `external_id`：`{manifest_record.external_id}`",
                    f"- 正文 SHA-256：`{manifest_record.content_hash}`",
                    (
                        "- 原标注备注："
                        + (source_record.review_note or "无")
                    ),
                    "",
                    "### 行号正文",
                    "",
                    "~~~text",
                    manifest_record.line_numbered_content.rstrip(),
                    "~~~",
                ]
            )
            if annotation_record.labels:
                for item in label_reviews_by_record[annotation_record.external_id]:
                    lines.extend(
                        [
                            "",
                            f"### `{item.review_id}`",
                            "",
                            f"- 原标签：`{_format_label(item.original_label)}`",
                            f"- 原证据：{_format_evidence(item.original_label)}",
                            (
                                "- 自动预审："
                                + (
                                    "证据行号与正文不匹配，不能直接 approve"
                                    if item.review_id in evidence_issue_ids
                                    else "证据行号与正文匹配"
                                )
                            ),
                            f"- 当前决策：`{item.decision.value}`",
                        ]
                    )
                    if item.corrected_label is not None:
                        lines.append(
                            f"- 修正标签：`{_format_label(item.corrected_label)}`"
                        )
                        lines.append(
                            f"- 修正证据：{_format_evidence(item.corrected_label)}"
                        )
                    lines.append(
                        f"- 复核备注：{item.review_note or '待填写'}"
                    )
                continue

            empty_review = empty_review_by_id[annotation_record.external_id]
            lines.extend(
                [
                    "",
                    f"### `{empty_review.review_id}`",
                    "",
                    "- 原结论：正文没有足以支持受控标签的直接证据。",
                    f"- 当前决策：`{empty_review.decision.value}`",
                ]
            )
            if empty_review.corrected_labels:
                lines.append(
                    "- 修正标签："
                    + "；".join(
                        f"`{_format_label(label)}`"
                        for label in empty_review.corrected_labels
                    )
                )
            lines.append(
                f"- 复核备注：{empty_review.review_note or '待填写'}"
            )
        lines.append("")
        return "\n".join(lines)

    def _validate_source_annotation(self) -> None:
        if self.annotation.status != DomainLabelAnnotationDraftStatus.READY_FOR_REVIEW:
            raise ValueError(
                "只有 ready_for_review 的 v2 标注草稿才能进入独立复核: "
                f"status={self.annotation.status.value}"
            )
        self.annotation_workspace.validate(self.annotation)

    def _validate_binding(self, review: DomainLabelGoldV2Review) -> None:
        if review.source_annotation_version != self.annotation.version:
            raise ValueError(
                "独立复核绑定的标注版本不一致: "
                f"review={review.source_annotation_version} "
                f"annotation={self.annotation.version}"
            )
        if review.source_annotation_sha256 != self.annotation_sha256:
            raise ValueError(
                "独立复核绑定的标注文件 SHA-256 不一致，禁止在已变化标注上复核: "
                f"review={review.source_annotation_sha256} "
                f"actual={self.annotation_sha256}"
            )
        if review.source_manifest_version != self.annotation.source_manifest_version:
            raise ValueError(
                "独立复核绑定的采样清单版本不一致: "
                f"review={review.source_manifest_version} "
                f"annotation={self.annotation.source_manifest_version}"
            )
        if review.source_manifest_sha256 != self.annotation.source_manifest_sha256:
            raise ValueError(
                "独立复核绑定的采样清单 SHA-256 不一致: "
                f"review={review.source_manifest_sha256} "
                f"annotation={self.annotation.source_manifest_sha256}"
            )
        if (
            review.source_manifest_version != self.manifest.version
            or review.source_manifest_sha256 != self.manifest_sha256
        ):
            raise ValueError("独立复核绑定的采样清单与当前冻结清单不一致")

    def _expected_review_keys(
        self,
    ) -> tuple[
        dict[str, tuple[DomainLabelAnnotationRecord, DomainLabelGoldLabel]],
        set[str],
    ]:
        labels: dict[
            str,
            tuple[DomainLabelAnnotationRecord, DomainLabelGoldLabel],
        ] = {}
        empty_ids: set[str] = set()
        for record in self.annotation.records:
            if not record.labels:
                empty_ids.add(record.external_id)
                continue
            for label in record.labels:
                labels[self._label_review_id(record.external_id, label)] = (
                    record,
                    label,
                )
        return labels, empty_ids

    @staticmethod
    def _validate_exact_coverage(
        *,
        expected_labels: set[str],
        actual_labels: set[str],
        expected_empty_ids: set[str],
        actual_empty_ids: set[str],
    ) -> None:
        missing_labels = sorted(expected_labels - actual_labels)
        extra_labels = sorted(actual_labels - expected_labels)
        missing_empty = sorted(expected_empty_ids - actual_empty_ids)
        extra_empty = sorted(actual_empty_ids - expected_empty_ids)
        if missing_labels or extra_labels or missing_empty or extra_empty:
            details: list[str] = []
            if missing_labels:
                details.append(f"missing_labels={', '.join(missing_labels)}")
            if extra_labels:
                details.append(f"extra_labels={', '.join(extra_labels)}")
            if missing_empty:
                details.append(f"missing_empty={', '.join(missing_empty)}")
            if extra_empty:
                details.append(f"extra_empty={', '.join(extra_empty)}")
            raise ValueError(
                "独立复核必须覆盖且只能覆盖全部人工标签和空标签结论: "
                + "; ".join(details)
            )

    @staticmethod
    def _build_final_label_map(
        review: DomainLabelGoldV2Review,
        *,
        record_by_id: dict[str, DomainLabelAnnotationRecord],
        expected_labels: dict[
            str,
            tuple[DomainLabelAnnotationRecord, DomainLabelGoldLabel],
        ],
        expected_empty_ids: set[str],
    ) -> dict[str, list[DomainLabelGoldLabel]]:
        final: dict[str, list[DomainLabelGoldLabel]] = {
            external_id: [] for external_id in record_by_id
        }
        for item in review.label_reviews:
            if item.decision == DomainLabelReviewDecision.APPROVE:
                final[item.external_id].append(item.original_label)
            elif item.decision == DomainLabelReviewDecision.NEEDS_CHANGE:
                if item.corrected_label is None:
                    raise ValueError(
                        f"needs_change 标签复核缺少 corrected_label: {item.review_id}"
                    )
                final[item.external_id].append(item.corrected_label)

        for item in review.empty_record_reviews:
            if item.decision == DomainLabelReviewDecision.NEEDS_CHANGE:
                final[item.external_id].extend(item.corrected_labels)
        return final

    def _validate_final_labels(
        self,
        final_labels: dict[str, list[DomainLabelGoldLabel]],
    ) -> None:
        draft = DomainLabelAnnotationDraft(
            version=self.annotation.version,
            source_manifest_version=self.annotation.source_manifest_version,
            source_manifest_sha256=self.annotation.source_manifest_sha256,
            source_manifest_generated_at=self.annotation.source_manifest_generated_at,
            status=DomainLabelAnnotationDraftStatus.IN_PROGRESS,
            records=[
                DomainLabelAnnotationRecord(
                    external_id=record.external_id,
                    annotation_status=DomainLabelAnnotationRecordStatus.REVIEWED,
                    labels=final_labels[record.external_id],
                    review_note=record.review_note,
                )
                for record in self.annotation.records
            ],
        )
        self.annotation_workspace.validate(draft)

    def _preflight_label_reviews(
        self,
        review: DomainLabelGoldV2Review,
        *,
        record_by_id: dict[str, DomainLabelSamplingRecord],
    ) -> list[str]:
        issues: list[str] = []
        for item in review.label_reviews:
            source_record = record_by_id[item.external_id]
            if item.decision == DomainLabelReviewDecision.APPROVE:
                if not _evidence_matches(source_record, item.original_label):
                    issues.append(item.review_id)
                continue
            if item.decision != DomainLabelReviewDecision.NEEDS_CHANGE:
                continue
            if item.corrected_label is None or not _evidence_matches(
                source_record,
                item.corrected_label,
            ):
                raise ValueError(
                    "修正标签的证据文本与行号不匹配: "
                    f"review_id={item.review_id}"
                )
        for item in review.empty_record_reviews:
            if item.decision != DomainLabelReviewDecision.NEEDS_CHANGE:
                continue
            source_record = record_by_id[item.external_id]
            for label in item.corrected_labels:
                if not _evidence_matches(source_record, label):
                    raise ValueError(
                        "空标签结论的修正标签证据与正文不匹配: "
                        f"review_id={item.review_id}"
                    )
        return issues

    @staticmethod
    def _blocking_review_ids(
        review: DomainLabelGoldV2Review,
        *,
        evidence_issue_ids: set[str],
    ) -> list[str]:
        blocking: list[str] = []
        for item in review.label_reviews:
            if item.decision in {
                DomainLabelReviewDecision.PENDING,
                DomainLabelReviewDecision.UNCERTAIN,
            } or item.review_id in evidence_issue_ids:
                blocking.append(item.review_id)
        for item in review.empty_record_reviews:
            if item.decision in {
                DomainLabelReviewDecision.PENDING,
                DomainLabelReviewDecision.REJECT,
                DomainLabelReviewDecision.UNCERTAIN,
            }:
                blocking.append(item.review_id)
        return blocking

    @staticmethod
    def _label_review_id(
        external_id: str,
        label: DomainLabelGoldLabel,
    ) -> str:
        return (
            f"label:{external_id}:{label.dimension.value}:"
            f"{normalize_lookup(label.name)}"
        )

    @staticmethod
    def _empty_record_review_id(external_id: str) -> str:
        return f"empty:{external_id}"


def format_domain_label_review_summary(
    summary: DomainLabelReviewValidationSummary,
) -> str:
    lines = [
        f"version={summary.version} status={summary.status.value}",
        (
            f"labels={summary.label_review_count} "
            f"decided_labels={summary.decided_label_review_count} "
            f"empty_records={summary.empty_record_review_count} "
            f"decided_empty_records={summary.decided_empty_record_review_count}"
        ),
        (
            f"pending={summary.pending_decision_count} "
            f"ready_to_freeze={str(summary.ready_to_freeze).lower()}"
        ),
        (
            "label_decisions "
            f"approve={summary.approve_label_count} "
            f"needs_change={summary.needs_change_label_count} "
            f"reject={summary.reject_label_count} "
            f"uncertain={summary.uncertain_label_count}"
        ),
        (
            "empty_record_decisions "
            f"approve={summary.approve_empty_record_count} "
            f"needs_change={summary.needs_change_empty_record_count} "
            f"reject={summary.reject_empty_record_count} "
            f"uncertain={summary.uncertain_empty_record_count}"
        ),
    ]
    if summary.blocking_review_ids:
        lines.append("blocking_review_ids:")
        lines.extend(
            f"- {review_id}" for review_id in summary.blocking_review_ids
        )
    return "\n".join(lines)


def _format_label(label: DomainLabelGoldLabel) -> str:
    return (
        f"{label.dimension.value}:{label.name} "
        f"severity={label.severity.value}"
    )


def _format_evidence(label: DomainLabelGoldLabel) -> str:
    return "；".join(
        (
            f"“{evidence.evidence_text}” "
            f"（{evidence.line_start}-{evidence.line_end}）"
        )
        for evidence in label.evidence
    )


def _evidence_matches(
    record: DomainLabelSamplingRecord,
    label: DomainLabelGoldLabel,
) -> bool:
    source_lines = _source_lines(record)
    for evidence in label.evidence:
        if (
            evidence.line_start not in source_lines
            or evidence.line_end not in source_lines
            or evidence.line_end < evidence.line_start
        ):
            return False
        source_text = "\n".join(
            source_lines[line_number]
            for line_number in range(
                evidence.line_start,
                evidence.line_end + 1,
            )
        )
        if _normalize_evidence(evidence.evidence_text) not in _normalize_evidence(
            source_text
        ):
            return False
    return True


def _source_lines(
    record: DomainLabelSamplingRecord,
) -> dict[int, str]:
    lines: dict[int, str] = {}
    for raw_line in record.line_numbered_content.splitlines():
        line_number_text, separator, content = raw_line.partition(" | ")
        if not separator:
            raise ValueError(
                "采样清单的行号正文格式非法: "
                f"external_id={record.external_id} line={raw_line}"
            )
        line_number = int(line_number_text)
        if line_number in lines:
            raise ValueError(
                "采样清单存在重复行号: "
                f"external_id={record.external_id} line={line_number}"
            )
        lines[line_number] = content.rstrip()
    return lines


def _normalize_evidence(text: str) -> str:
    return re.sub(r"\s+", "", text)
