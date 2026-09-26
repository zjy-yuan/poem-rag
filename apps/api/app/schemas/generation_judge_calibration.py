from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.generation_judge import (
    FaithfulnessVerdict,
    RelevanceVerdict,
)

CalibrationDirection = Literal["match", "judge_stricter", "judge_looser"]


class GenerationJudgeHumanReview(BaseModel):
    case_id: str = Field(min_length=1, max_length=200)
    relevance_0_2: int = Field(ge=0, le=2)
    faithfulness_0_2: int = Field(ge=0, le=2)
    notes: str = Field(default="", max_length=1000)


class GenerationJudgeCalibrationCase(BaseModel):
    case_id: str
    category: str
    human_relevance_0_2: int
    judge_relevance: RelevanceVerdict
    judge_relevance_0_2: int
    relevance_exact_match: bool
    human_faithfulness_0_2: int
    judge_faithfulness: FaithfulnessVerdict
    judge_faithfulness_0_2: int
    faithfulness_exact_match: bool
    human_strict_pass: bool
    judge_strict_pass: bool
    strict_pass_match: bool
    strict_pass_direction: CalibrationDirection
    notes: str


class GenerationJudgeCalibrationSummary(BaseModel):
    eligible_cases: int
    reviewed_cases: int
    coverage_rate: float
    relevance_exact_agreement_rate: float
    faithfulness_exact_agreement_rate: float
    strict_pass_agreement_rate: float
    judge_stricter_cases: int
    judge_looser_cases: int


class GenerationJudgeCalibrationReport(BaseModel):
    source_dataset_version: str
    source_generated_at: datetime
    generator_model: str
    judge_model: str
    reviewed_at: datetime
    summary: GenerationJudgeCalibrationSummary
    results: list[GenerationJudgeCalibrationCase]
