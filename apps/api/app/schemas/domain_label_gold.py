from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.text import normalize_lookup
from app.models.domain_label import DomainLabelDimension


class DomainLabelGoldSeverity(StrEnum):
    NORMAL = "normal"
    CRITICAL = "critical"


class DomainLabelGoldSource(BaseModel):
    source_key: str = Field(min_length=1, max_length=100)
    source_name: str = Field(min_length=1, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    license_note: str = Field(min_length=1, max_length=500)

    @field_validator("source_key", "source_name", "license_note")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("金标准来源字段不能为空")
        return normalized

    @field_validator("source_url")
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DomainLabelGoldEvidence(BaseModel):
    evidence_text: str = Field(min_length=1, max_length=20000)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @field_validator("evidence_text")
    @classmethod
    def normalize_evidence_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("金标准证据文本不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_line_range(self) -> DomainLabelGoldEvidence:
        if self.line_end < self.line_start:
            raise ValueError("line_end 不能小于 line_start")
        return self


class DomainLabelGoldLabel(BaseModel):
    dimension: DomainLabelDimension
    name: str = Field(min_length=1, max_length=80)
    severity: DomainLabelGoldSeverity = DomainLabelGoldSeverity.NORMAL
    note: str | None = Field(default=None, max_length=500)
    evidence: list[DomainLabelGoldEvidence] = Field(min_length=1, max_length=10)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("金标准标签名称不能为空")
        return normalized

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def lookup_key(self) -> tuple[str, str]:
        return self.dimension.value, normalize_lookup(self.name)


class DomainLabelGoldRecord(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    author: str = Field(min_length=1, max_length=120)
    expected_source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_note: str | None = Field(default=None, max_length=1000)
    labels: list[DomainLabelGoldLabel] = Field(default_factory=list, max_length=50)

    @field_validator("external_id", "title", "author")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("金标准作品字段不能为空")
        return normalized

    @field_validator("review_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("labels")
    @classmethod
    def deduplicate_labels(
        cls,
        values: list[DomainLabelGoldLabel],
    ) -> list[DomainLabelGoldLabel]:
        unique: list[DomainLabelGoldLabel] = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            if value.lookup_key in seen:
                raise ValueError(
                    "同一金标准作品内不能重复标签: "
                    f"dimension={value.dimension.value} name={value.name}"
                )
            seen.add(value.lookup_key)
            unique.append(value)
        return unique


class DomainLabelGoldDataset(BaseModel):
    version: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    selection_strategy: str = Field(min_length=1, max_length=1000)
    source: DomainLabelGoldSource
    poem_source_key: str = Field(min_length=1, max_length=100)
    target_record_counts: dict[DomainLabelDimension, int]
    records: list[DomainLabelGoldRecord] = Field(min_length=1)

    @field_validator("version", "name", "description", "selection_strategy", "poem_source_key")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("金标准数据集必填字段不能为空")
        return normalized

    @field_validator("target_record_counts")
    @classmethod
    def validate_target_counts(
        cls,
        value: dict[DomainLabelDimension, int],
    ) -> dict[DomainLabelDimension, int]:
        if set(value) != set(DomainLabelDimension):
            raise ValueError("target_record_counts 必须包含全部四个标签维度")
        if any(count < 0 for count in value.values()):
            raise ValueError("target_record_counts 不能为负数")
        if not any(count > 0 for count in value.values()):
            raise ValueError("target_record_counts 至少需要一个正数目标")
        return value

    @model_validator(mode="after")
    def validate_records(self) -> DomainLabelGoldDataset:
        external_ids = [record.external_id for record in self.records]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("同一金标准数据集中的 external_id 必须唯一")
        if not any(record.labels for record in self.records):
            raise ValueError("金标准数据集至少需要一条标签")
        return self


class DomainLabelMetricValues(BaseModel):
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    gold_count: int = Field(ge=0)
    predicted_count: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)


class DomainLabelDimensionMetric(DomainLabelMetricValues):
    dimension: str


class DomainLabelMetricSummary(BaseModel):
    micro: DomainLabelMetricValues
    macro: DomainLabelMetricValues
    macro_dimension_count: int = Field(ge=0)
    dimensions: dict[str, DomainLabelDimensionMetric]


class DomainLabelGoldDimensionCoverage(BaseModel):
    dimension: str
    target_record_count: int = Field(ge=0)
    gold_record_count: int = Field(ge=0)
    predicted_record_count: int = Field(ge=0)
    plan_coverage_rate: float = Field(ge=0, le=1)
    plan_gap_count: int = Field(ge=0)
    predicted_in_scope_rate: float = Field(ge=0, le=1)


class DomainLabelGoldCoverageSummary(BaseModel):
    published_poem_count: int = Field(ge=0)
    gold_record_count: int = Field(ge=0)
    gold_scope_coverage_rate: float = Field(ge=0, le=1)
    records_with_predictions: int = Field(ge=0)
    records_without_predictions: int = Field(ge=0)
    macro_plan_coverage_rate: float = Field(ge=0, le=1)
    total_plan_gap_count: int = Field(ge=0)
    dimensions: dict[str, DomainLabelGoldDimensionCoverage]


class DomainLabelGoldQualitySummary(BaseModel):
    gold_label_count: int = Field(ge=0)
    critical_gold_label_count: int = Field(ge=0)
    predicted_label_count: int = Field(ge=0)
    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    critical_missing_label_count: int = Field(ge=0)
    critical_error_rate: float = Field(ge=0, le=1)
    evidence_error_count: int = Field(ge=0)
    evidence_error_rate: float = Field(ge=0, le=1)
    approved_assignment_count: int = Field(ge=0)


class DomainLabelGoldMissingLabel(BaseModel):
    dimension: str
    canonical_name: str
    severity: DomainLabelGoldSeverity
    expected_evidence: list[DomainLabelGoldEvidence]


class DomainLabelGoldExtraLabel(BaseModel):
    dimension: str
    label_id: int
    canonical_name: str
    generation_method: str
    evidence_text: str | None
    line_start: int | None
    line_end: int | None


class DomainLabelGoldEvidenceIssue(BaseModel):
    dimension: str
    canonical_name: str
    severity: DomainLabelGoldSeverity
    expected_evidence: list[DomainLabelGoldEvidence]
    observed_evidence_text: str | None
    observed_line_start: int | None
    observed_line_end: int | None
    reason: str


class DomainLabelGoldRecordReport(BaseModel):
    external_id: str
    poem_id: int
    version_id: int
    title: str
    author: str
    gold_label_count: int = Field(ge=0)
    predicted_label_count: int = Field(ge=0)
    missing_labels: list[DomainLabelGoldMissingLabel]
    extra_labels: list[DomainLabelGoldExtraLabel]
    evidence_issues: list[DomainLabelGoldEvidenceIssue]
    critical_missing_label_count: int = Field(ge=0)


class DomainLabelGoldEvaluationReport(BaseModel):
    generated_at: datetime
    dataset_version: str
    dataset_name: str
    selection_strategy: str
    source_key: str
    poem_source_key: str
    metrics: DomainLabelMetricSummary
    coverage: DomainLabelGoldCoverageSummary
    quality: DomainLabelGoldQualitySummary
    records: list[DomainLabelGoldRecordReport]
