from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from app.core.text import normalize_lookup
from app.db.seed import DOMAIN_LABELS
from app.models.domain_label import DomainLabelDimension
from app.schemas.domain_label_annotation import (
    DomainLabelAnnotationDraft,
    DomainLabelAnnotationRecord,
    DomainLabelAnnotationRecordStatus,
    DomainLabelAnnotationValidationSummary,
)
from app.schemas.domain_label_sampling import (
    DomainLabelSamplingManifest,
    DomainLabelSamplingRecord,
)

ANNOTATION_DRAFT_VERSION = "domain-label-gold-v2-annotation"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_sampling_manifest(path: Path) -> DomainLabelSamplingManifest:
    return DomainLabelSamplingManifest.model_validate_json(path.read_bytes())


class DomainLabelAnnotationWorkspace:
    """Create and validate a label-blind manual annotation workspace."""

    def __init__(
        self,
        manifest: DomainLabelSamplingManifest,
        manifest_sha256: str,
    ) -> None:
        self.manifest = manifest
        self.manifest_sha256 = manifest_sha256

    @classmethod
    def from_path(cls, manifest_path: Path) -> DomainLabelAnnotationWorkspace:
        return cls(
            manifest=load_sampling_manifest(manifest_path),
            manifest_sha256=sha256_file(manifest_path),
        )

    def create_draft(self) -> DomainLabelAnnotationDraft:
        return DomainLabelAnnotationDraft(
            version=ANNOTATION_DRAFT_VERSION,
            source_manifest_version=self.manifest.version,
            source_manifest_sha256=self.manifest_sha256,
            source_manifest_generated_at=self.manifest.generated_at,
            records=[
                DomainLabelAnnotationRecord(external_id=record.external_id)
                for record in self.manifest.records
            ],
        )

    def validate(
        self,
        draft: DomainLabelAnnotationDraft,
    ) -> DomainLabelAnnotationValidationSummary:
        self._validate_manifest_binding(draft)
        records_by_id = self._validate_record_coverage(draft)
        controlled_names = _controlled_label_names_by_dimension()

        dimension_counts: Counter[str] = Counter()
        critical_label_count = 0
        for record in draft.records:
            source_record = records_by_id[record.external_id]
            for label in record.labels:
                allowed_names = controlled_names[label.dimension.value]
                if normalize_lookup(label.name) not in allowed_names:
                    raise ValueError(
                        "人工标注使用了非受控标签: "
                        f"external_id={record.external_id} "
                        f"dimension={label.dimension.value} name={label.name}"
                    )
                for evidence in label.evidence:
                    if evidence.line_end > source_record.line_count:
                        raise ValueError(
                            "人工标注证据行号超出冻结正文范围: "
                            f"external_id={record.external_id} "
                            f"dimension={label.dimension.value} name={label.name} "
                            f"line_end={evidence.line_end} "
                            f"line_count={source_record.line_count}"
                        )
                dimension_counts[label.dimension.value] += 1
                critical_label_count += label.severity.value == "critical"

        reviewed_record_count = sum(
            record.annotation_status
            == DomainLabelAnnotationRecordStatus.REVIEWED
            for record in draft.records
        )
        pending_external_ids = [
            record.external_id
            for record in draft.records
            if record.annotation_status
            != DomainLabelAnnotationRecordStatus.REVIEWED
        ]
        return DomainLabelAnnotationValidationSummary(
            record_count=len(draft.records),
            reviewed_record_count=reviewed_record_count,
            pending_record_count=len(pending_external_ids),
            label_count=sum(dimension_counts.values()),
            critical_label_count=critical_label_count,
            dimension_counts={
                dimension.value: dimension_counts[dimension.value]
                for dimension in DomainLabelDimension
            },
            pending_external_ids=pending_external_ids,
        )

    def export_review_markdown(self) -> str:
        lines = [
            "# 领域标签独立人工盲标审阅稿",
            "",
            f"> 采样清单：`{self.manifest.version}`",
            f"> 清单 SHA-256：`{self.manifest_sha256}`",
            f"> 清单生成时间：`{self.manifest.generated_at.isoformat()}`",
            "> 标注文件：`data/eval/domain_label_gold_v2_annotation.json`",
            "",
            "只依据每首作品正文中的直接证据填写标签；作品常识、作者生平、模型记忆和",
            "检索预测都不能替代正文证据。空行计入行号，`line_start` 与 `line_end`",
            "均为包含关系。没有受控标签时保留空列表。",
            "",
            "## 受控标签",
            "",
        ]
        for dimension in DomainLabelDimension:
            names = sorted(
                {
                    str(item["name"])
                    for item in DOMAIN_LABELS
                    if item["dimension"] == dimension.value
                }
            )
            lines.append(f"- `{dimension.value}`：{'、'.join(names)}")

        for index, record in enumerate(self.manifest.records, start=1):
            lines.extend(
                [
                    "",
                    f"## {index}. {record.title}",
                    "",
                    f"- 作者：{record.author}",
                    f"- 朝代：{record.dynasty}",
                    f"- 分层：`{record.stratum.value}`",
                    f"- `external_id`：`{record.external_id}`",
                    f"- 正文 SHA-256：`{record.content_hash}`",
                    f"- 来源：{record.source_name or '未知'}",
                    f"- 来源 URL：{record.source_url or '无'}",
                    "",
                    "### 行号正文",
                    "",
                    "~~~text",
                    "\n".join(
                        line.rstrip()
                        for line in record.line_numbered_content.splitlines()
                    ),
                    "~~~",
                    "",
                    "### 人工标注位",
                    "",
                    "- imagery：",
                    "- emotion：",
                    "- theme：",
                    "- allusion：",
                    "- review_note：",
                ]
            )
            if index == len(self.manifest.records):
                lines.append("")
        return "\n".join(lines)

    def _validate_manifest_binding(
        self,
        draft: DomainLabelAnnotationDraft,
    ) -> None:
        if draft.source_manifest_version != self.manifest.version:
            raise ValueError(
                "标注草稿绑定的采样清单版本不一致: "
                f"draft={draft.source_manifest_version} manifest={self.manifest.version}"
            )
        if draft.source_manifest_sha256 != self.manifest_sha256:
            raise ValueError(
                "标注草稿绑定的采样清单 SHA-256 不一致，禁止在已变化清单上标注: "
                f"draft={draft.source_manifest_sha256} "
                f"actual={self.manifest_sha256}"
            )
        if draft.source_manifest_generated_at != self.manifest.generated_at:
            raise ValueError(
                "标注草稿绑定的采样清单生成时间不一致: "
                f"draft={draft.source_manifest_generated_at.isoformat()} "
                f"manifest={self.manifest.generated_at.isoformat()}"
            )

    def _validate_record_coverage(
        self,
        draft: DomainLabelAnnotationDraft,
    ) -> dict[str, DomainLabelSamplingRecord]:
        records_by_id = {record.external_id: record for record in self.manifest.records}
        expected_ids = set(records_by_id)
        actual_ids = {record.external_id for record in draft.records}
        missing_ids = sorted(expected_ids - actual_ids)
        extra_ids = sorted(actual_ids - expected_ids)
        if missing_ids or extra_ids:
            details: list[str] = []
            if missing_ids:
                details.append(f"missing={', '.join(missing_ids)}")
            if extra_ids:
                details.append(f"extra={', '.join(extra_ids)}")
            raise ValueError(
                "标注草稿必须覆盖且只能覆盖冻结采样清单中的全部作品: "
                + "; ".join(details)
            )
        return records_by_id


def format_annotation_validation_summary(
    summary: DomainLabelAnnotationValidationSummary,
) -> str:
    lines = [
        (
            f"records={summary.record_count} "
            f"reviewed={summary.reviewed_record_count} "
            f"pending={summary.pending_record_count} "
            f"labels={summary.label_count} "
            f"critical={summary.critical_label_count}"
        ),
        "dimensions:",
    ]
    lines.extend(
        f"- {dimension}: {summary.dimension_counts.get(dimension.value, 0)}"
        for dimension in DomainLabelDimension
    )
    if summary.pending_external_ids:
        lines.append("pending_external_ids:")
        lines.extend(f"- {external_id}" for external_id in summary.pending_external_ids)
    return "\n".join(lines)


def _controlled_label_names_by_dimension() -> dict[str, set[str]]:
    controlled_names = {dimension.value: set() for dimension in DomainLabelDimension}
    for item in DOMAIN_LABELS:
        controlled_names[str(item["dimension"])].add(normalize_lookup(str(item["name"])))
    return controlled_names
