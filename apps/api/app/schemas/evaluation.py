from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.chunk import ChunkGranularity
from app.schemas.retrieval import RetrievalEvidence


class EvidenceSelector(BaseModel):
    poem_title: str | None = Field(default=None, min_length=1)
    author_name: str | None = Field(default=None, min_length=1)
    dynasty_name: str | None = Field(default=None, min_length=1)
    granularity: ChunkGranularity | None = None
    text_contains: str | None = Field(default=None, min_length=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_selector(self) -> EvidenceSelector:
        locator_fields = (
            self.poem_title,
            self.author_name,
            self.dynasty_name,
            self.granularity,
            self.text_contains,
            self.line_start,
            self.line_end,
        )
        if not any(value is not None for value in locator_fields):
            raise ValueError("金标准证据选择器至少需要一个定位条件")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end 不能小于 line_start")
        return self


class RetrievalEvaluationCase(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    category: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=200)
    expected: Literal["evidence", "no_evidence"]
    gold_evidence: list[EvidenceSelector] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expected_evidence(self) -> RetrievalEvaluationCase:
        if self.expected == "evidence" and not self.gold_evidence:
            raise ValueError("expected=evidence 的评估样本必须提供金标准证据")
        if self.expected == "no_evidence" and self.gold_evidence:
            raise ValueError("expected=no_evidence 的评估样本不能提供金标准证据")
        return self


class RetrievalEvaluationDataset(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    cases: list[RetrievalEvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_case_ids(self) -> RetrievalEvaluationDataset:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("评估样本 ID 必须唯一")
        return self


class RetrievalEvidenceSnapshot(BaseModel):
    chunk_id: int
    poem_id: int
    poem_version_id: int
    title: str
    granularity: ChunkGranularity
    chunk_index: int
    line_start: int | None
    line_end: int | None
    text: str
    score: float
    match_types: list[str]

    @classmethod
    def from_evidence(cls, evidence: RetrievalEvidence) -> RetrievalEvidenceSnapshot:
        return cls(
            chunk_id=evidence.chunk_id,
            poem_id=evidence.poem_id,
            poem_version_id=evidence.poem_version_id,
            title=evidence.title,
            granularity=evidence.granularity,
            chunk_index=evidence.chunk_index,
            line_start=evidence.line_start,
            line_end=evidence.line_end,
            text=evidence.text,
            score=evidence.score,
            match_types=evidence.match_types,
        )


class RetrievalEvaluationCaseResult(BaseModel):
    case_id: str
    category: str
    question: str
    expected: Literal["evidence", "no_evidence"]
    passed: bool
    retrieved_count: int
    recall_at_k: float | None
    reciprocal_rank: float | None
    matched_gold: list[int]
    missing_gold: list[int]
    latency_ms: float
    retrieved_evidence: list[RetrievalEvidenceSnapshot]


class RetrievalEvaluationSummary(BaseModel):
    total_cases: int
    answerable_cases: int
    unanswerable_cases: int
    passed_cases: int
    pass_rate: float
    recall_at_k: float | None
    mrr: float | None
    hit_rate_at_k: float | None
    answerable_no_result_rate: float | None
    unanswerable_accuracy: float | None
    refusal_precision: float | None
    refusal_recall: float | None
    refusal_f1: float | None
    average_latency_ms: float
    p95_latency_ms: float


class RetrievalEvaluationReport(BaseModel):
    dataset_version: str
    strategy: str
    top_k: int
    generated_at: datetime
    summary: RetrievalEvaluationSummary
    categories: dict[str, RetrievalEvaluationSummary]
    failure_case_ids: list[str]
    results: list[RetrievalEvaluationCaseResult]
