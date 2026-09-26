from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ClaimSupport = Literal["supported", "partially_supported", "unsupported"]
FaithfulnessVerdict = Literal["supported", "partially_supported", "unsupported"]
RelevanceVerdict = Literal["relevant", "partially_relevant", "irrelevant"]
JudgeStatus = Literal[
    "judged",
    "skipped_refusal",
    "skipped_error",
    "skipped_empty",
    "judge_error",
]


class GenerationJudgeClaim(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    citation_ranks: list[int] = Field(default_factory=list, max_length=20)
    support: ClaimSupport

    @model_validator(mode="after")
    def validate_claim(self) -> GenerationJudgeClaim:
        if not self.text.strip():
            raise ValueError("事实声明不能为空")
        if any(rank <= 0 for rank in self.citation_ranks):
            raise ValueError("引用 rank 必须大于 0")
        if len(self.citation_ranks) != len(set(self.citation_ranks)):
            raise ValueError("同一事实声明的引用 rank 不能重复")
        if self.support == "supported" and not self.citation_ranks:
            raise ValueError("supported 事实声明必须关联引用")
        return self


class GenerationJudgeAssessment(BaseModel):
    relevance: RelevanceVerdict
    faithfulness: FaithfulnessVerdict
    claims: list[GenerationJudgeClaim] = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_faithfulness(self) -> GenerationJudgeAssessment:
        if not self.reason.strip():
            raise ValueError("判定理由不能为空")
        derived = _derive_faithfulness(self.claims)
        if self.faithfulness != derived:
            raise ValueError("faithfulness 与事实声明支撑状态不一致")
        return self


class GenerationJudgeCaseResult(BaseModel):
    case_id: str
    category: str
    question: str
    expected: Literal["answer", "refusal"]
    deterministic_passed: bool
    deterministic_answer_correct: bool
    refused: bool
    answer: str
    citation_count: int
    judge_status: JudgeStatus
    assessment: GenerationJudgeAssessment | None
    error_code: str | None
    latency_ms: float
    citations: list[dict[str, object]]


class GenerationJudgeSummary(BaseModel):
    total_cases: int
    answerable_cases: int
    refusal_cases: int
    judged_cases: int
    judge_error_cases: int
    deterministic_pass_rate: float
    faithfulness_pass_rate: float | None
    answer_relevance_rate: float | None
    claim_support_rate: float | None
    average_unsupported_claims: float | None
    refusal_accuracy: float | None


class GenerationJudgeReport(BaseModel):
    source_dataset_version: str
    source_generated_at: datetime
    generator_model: str
    judge_model: str
    generated_at: datetime
    summary: GenerationJudgeSummary
    categories: dict[str, GenerationJudgeSummary]
    deterministic_failure_case_ids: list[str]
    judge_failure_case_ids: list[str]
    results: list[GenerationJudgeCaseResult]


def _derive_faithfulness(
    claims: list[GenerationJudgeClaim],
) -> FaithfulnessVerdict:
    statuses = {claim.support for claim in claims}
    if statuses == {"supported"}:
        return "supported"
    if statuses == {"unsupported"}:
        return "unsupported"
    return "partially_supported"
