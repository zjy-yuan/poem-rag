from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.evaluation import EvidenceSelector


class FactRequirement(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    any_of: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_terms(self) -> FactRequirement:
        if any(not term.strip() for term in self.any_of):
            raise ValueError("事实候选词不能为空")
        if len(self.any_of) != len(set(self.any_of)):
            raise ValueError("同一事实的候选词不能重复")
        return self


class GenerationEvaluationCase(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]+$")
    category: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=300)
    expected: Literal["answer", "refusal"]
    required_facts: list[FactRequirement] = Field(default_factory=list, max_length=20)
    forbidden_facts: list[FactRequirement] = Field(default_factory=list, max_length=20)
    expected_citations: list[EvidenceSelector] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_expectation(self) -> GenerationEvaluationCase:
        fact_ids = [
            fact.id
            for fact in [*self.required_facts, *self.forbidden_facts]
        ]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("同一评估样本中的事实 ID 必须唯一")

        if self.expected == "answer":
            if not self.required_facts:
                raise ValueError("expected=answer 的评估样本必须提供 required_facts")
            if not self.expected_citations:
                raise ValueError("expected=answer 的评估样本必须提供 expected_citations")
        elif self.required_facts or self.forbidden_facts or self.expected_citations:
            raise ValueError("expected=refusal 的评估样本不能提供事实或引用金标准")

        for selector in self.expected_citations:
            if selector.line_start is not None or selector.line_end is not None:
                raise ValueError("生成层引用选择器不支持 line_start 或 line_end")
        return self


class GenerationEvaluationDataset(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    cases: list[GenerationEvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_cases(self) -> GenerationEvaluationDataset:
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("生成评估样本 ID 必须唯一")
        questions = [case.question for case in self.cases]
        if len(questions) != len(set(questions)):
            raise ValueError("生成评估样本 question 必须唯一")
        return self


class GenerationCitationSnapshot(BaseModel):
    chunk_id: int
    poem_id: int
    poem_version_id: int
    annotation_id: int | None
    title: str
    author_name: str | None
    dynasty_name: str | None
    granularity: str
    text: str
    score: float
    rank: int


class GenerationEvaluationCaseResult(BaseModel):
    case_id: str
    category: str
    question: str
    expected: Literal["answer", "refusal"]
    passed: bool
    answer_correct: bool
    refused: bool
    answer: str
    finish_reason: str | None
    assessment_status: str | None
    assessment_reason_code: str | None
    error_code: str | None
    required_fact_matches: dict[str, str]
    required_facts_missing: list[str]
    forbidden_facts_present: list[str]
    citation_count: int
    matched_citation_ranks: list[int]
    matched_expected_citation_indexes: list[int]
    missing_expected_citation_indexes: list[int]
    citation_precision: float | None
    citation_recall: float | None
    retrieval_candidate_count: int
    retrieval_selected_count: int
    strategy: str | None
    latency_ms: float
    ttft_ms: float | None = None
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    citations: list[GenerationCitationSnapshot]


class GenerationEvaluationSummary(BaseModel):
    total_cases: int
    answerable_cases: int
    refusal_cases: int
    passed_cases: int
    pass_rate: float
    answer_accuracy: float | None
    refusal_accuracy: float | None
    refusal_precision: float | None
    refusal_recall: float | None
    refusal_f1: float | None
    citation_precision: float | None
    citation_recall: float | None
    average_latency_ms: float
    p95_latency_ms: float
    average_ttft_ms: float | None = None
    p95_ttft_ms: float | None = None
    average_stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    p95_stage_timings_ms: dict[str, float] = Field(default_factory=dict)


class GenerationEvaluationReport(BaseModel):
    dataset_version: str
    model: str
    strategy: str | None
    generated_at: datetime
    concurrency: int = Field(default=1, ge=1)
    wall_time_ms: float | None = None
    throughput_cases_per_second: float | None = None
    summary: GenerationEvaluationSummary
    categories: dict[str, GenerationEvaluationSummary]
    failure_case_ids: list[str]
    results: list[GenerationEvaluationCaseResult]
