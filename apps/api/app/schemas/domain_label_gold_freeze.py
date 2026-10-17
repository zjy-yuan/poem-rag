from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class DomainLabelCriticalAuditIssue(BaseModel):
    """One critical label whose evidence misses every strong surface form."""

    external_id: str = Field(min_length=1, max_length=255)
    dimension: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=80)
    evidence_text: str = Field(min_length=1, max_length=20000)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("external_id", "dimension", "name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("critical 审计问题字段不能为空")
        return normalized

    @field_validator("evidence_text", "reason")
    @classmethod
    def normalize_detail_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("critical 审计问题详情不能为空")
        return normalized


class DomainLabelGoldFreezeSummary(BaseModel):
    version: str
    review_version: str
    record_count: int = Field(ge=0)
    label_count: int = Field(ge=0)
    critical_label_count: int = Field(ge=0)
    empty_record_count: int = Field(ge=0)
    dimension_label_counts: dict[str, int]
    dimension_record_counts: dict[str, int]
    audit_issue_count: int = Field(ge=0)
    audit_issues: list[DomainLabelCriticalAuditIssue] = Field(default_factory=list)


class DomainLabelCriticalAuditReport(BaseModel):
    version: str
    generated_at: datetime
    dataset_version: str
    critical_rule_count: int = Field(ge=0)
    audited_critical_label_count: int = Field(ge=0)
    issue_count: int = Field(ge=0)
    issues: list[DomainLabelCriticalAuditIssue] = Field(default_factory=list)
