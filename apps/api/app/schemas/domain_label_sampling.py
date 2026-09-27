from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class DomainLabelSamplingStatus(StrEnum):
    PENDING = "pending"


class DomainLabelSamplingStratum(StrEnum):
    TANG_POEM = "tang-poem"
    SONG_WORK = "song-work"
    OTHER_DYNASTY = "other-dynasty"
    LONG_TEXT_OR_CI = "long-text-or-ci"


class DomainLabelSamplingLabelDraft(BaseModel):
    """Blank manual-label slot; the sampler never pre-populates label guesses."""

    name: str = Field(min_length=1, max_length=80)
    severity: str = Field(default="normal", pattern=r"^(normal|critical)$")
    note: str | None = Field(default=None, max_length=500)
    evidence: list[dict[str, object]] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("人工标签名称不能为空")
        return normalized

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DomainLabelSamplingManualLabels(BaseModel):
    imagery: list[DomainLabelSamplingLabelDraft] = Field(default_factory=list)
    emotion: list[DomainLabelSamplingLabelDraft] = Field(default_factory=list)
    theme: list[DomainLabelSamplingLabelDraft] = Field(default_factory=list)
    allusion: list[DomainLabelSamplingLabelDraft] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            (
                self.imagery,
                self.emotion,
                self.theme,
                self.allusion,
            )
        )


class DomainLabelSamplingRecord(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    poem_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=255)
    author: str = Field(min_length=1, max_length=120)
    dynasty: str = Field(min_length=1, max_length=80)
    source_name: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_count: int = Field(gt=0)
    line_numbered_content: str = Field(min_length=1)
    stratum: DomainLabelSamplingStratum
    annotation_status: DomainLabelSamplingStatus = DomainLabelSamplingStatus.PENDING
    manual_labels: DomainLabelSamplingManualLabels = Field(
        default_factory=DomainLabelSamplingManualLabels
    )
    review_note: str | None = Field(default=None, max_length=1000)

    @field_validator("external_id", "title", "author", "dynasty")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("采样记录必填文本不能为空")
        return normalized

    @field_validator("source_name", "source_url", "review_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_line_count(self) -> DomainLabelSamplingRecord:
        actual_line_count = len(self.line_numbered_content.splitlines())
        if actual_line_count != self.line_count:
            raise ValueError("line_count 与 line_numbered_content 行数不一致")
        return self


class DomainLabelSamplingManifest(BaseModel):
    version: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    selection_strategy: str = Field(min_length=1, max_length=1000)
    source_key: str = Field(min_length=1, max_length=100)
    random_seed: str = Field(min_length=1, max_length=200)
    requested_count: int = Field(ge=4)
    sampled_count: int = Field(ge=4)
    generated_at: datetime
    excluded_external_ids: list[str]
    records: list[DomainLabelSamplingRecord] = Field(min_length=4)

    @field_validator(
        "version",
        "name",
        "description",
        "selection_strategy",
        "source_key",
        "random_seed",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("采样清单必填字段不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_manifest(self) -> DomainLabelSamplingManifest:
        external_ids = [record.external_id for record in self.records]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("采样清单中的 external_id 必须唯一")
        if self.sampled_count != len(self.records):
            raise ValueError("sampled_count 必须等于 records 数量")
        if self.sampled_count > self.requested_count:
            raise ValueError("sampled_count 不能大于 requested_count")
        excluded = set(self.excluded_external_ids)
        if excluded & set(external_ids):
            raise ValueError("采样记录不能包含被排除的 external_id")
        for record in self.records:
            if not record.manual_labels.is_empty():
                raise ValueError("采样清单不能预填人工标签")
        return self
