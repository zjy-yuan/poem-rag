from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DomainLabelAuditScope(BaseModel):
    published_poem_count: int = Field(ge=0)
    current_version_count: int = Field(ge=0)
    published_poems_without_current_version: int = Field(ge=0)


class DomainLabelDimensionCoverage(BaseModel):
    dimension: str
    total_label_count: int = Field(ge=0)
    active_label_count: int = Field(ge=0)
    approved_assignment_count: int = Field(ge=0)
    online_label_count: int = Field(ge=0)
    covered_poem_count: int = Field(ge=0)
    coverage_rate: float = Field(ge=0, le=1)
    average_online_label_count_per_covered_poem: float = Field(ge=0)


class DomainLabelCoverageSummary(BaseModel):
    current_poems_with_any_assignment: int = Field(ge=0)
    current_poems_with_any_approved_assignment: int = Field(ge=0)
    current_poems_with_online_visible_label: int = Field(ge=0)
    current_poems_without_any_assignment: int = Field(ge=0)
    current_poems_without_online_visible_label: int = Field(ge=0)
    any_assignment_coverage_rate: float = Field(ge=0, le=1)
    approved_assignment_coverage_rate: float = Field(ge=0, le=1)
    online_visible_coverage_rate: float = Field(ge=0, le=1)
    dimensions: dict[str, DomainLabelDimensionCoverage]


class DomainLabelReviewSummary(BaseModel):
    current_assignment_count: int = Field(ge=0)
    review_status_counts: dict[str, int]
    generation_method_counts: dict[str, int]
    review_status_by_generation_method: dict[str, dict[str, int]]
    label_status_counts: dict[str, int]


class DomainLabelNumericDistribution(BaseModel):
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    mean: float = Field(ge=0)
    p50: float = Field(ge=0)
    p90: float = Field(ge=0)


class DomainLabelDistributionSummary(BaseModel):
    current_assignments_per_poem: DomainLabelNumericDistribution
    approved_assignments_per_poem: DomainLabelNumericDistribution
    online_visible_labels_per_poem: DomainLabelNumericDistribution


class DomainLabelGovernanceRisks(BaseModel):
    non_current_version_assignments: int = Field(ge=0)
    unpublished_or_deleted_poem_assignments: int = Field(ge=0)
    approved_assignments_with_merged_labels: int = Field(ge=0)
    approved_assignments_with_deprecated_labels: int = Field(ge=0)
    active_labels_without_online_visible_assignments: int = Field(ge=0)
    public_or_ai_assignments_without_source: int = Field(ge=0)
    ai_assignments_missing_metadata: int = Field(ge=0)
    invalid_merge_targets: int = Field(ge=0)
    origin_ref_generation_method_mismatches: int = Field(ge=0)
    cross_dimension_aliases: int = Field(ge=0)


class DomainLabelAuditReport(BaseModel):
    generated_at: datetime
    scope: DomainLabelAuditScope
    coverage: DomainLabelCoverageSummary
    review: DomainLabelReviewSummary
    distributions: DomainLabelDistributionSummary
    risks: DomainLabelGovernanceRisks
