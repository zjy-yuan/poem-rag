from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import UTC, datetime

from app.schemas.generation_judge import (
    GenerationJudgeCaseResult,
    GenerationJudgeReport,
)
from app.schemas.generation_judge_calibration import (
    CalibrationDirection,
    GenerationJudgeCalibrationCase,
    GenerationJudgeCalibrationReport,
    GenerationJudgeCalibrationSummary,
    GenerationJudgeHumanReview,
)

_RELEVANCE_SCORES = {
    "irrelevant": 0,
    "partially_relevant": 1,
    "relevant": 2,
}
_FAITHFULNESS_SCORES = {
    "unsupported": 0,
    "partially_supported": 1,
    "supported": 2,
}
_REQUIRED_COLUMNS = {
    "case_id",
    "relevance_0_2",
    "faithfulness_0_2",
}


def export_calibration_template(
    report: GenerationJudgeReport,
    *,
    case_ids: set[str] | None = None,
) -> str:
    results = _judged_results(report, case_ids=case_ids)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "case_id",
            "category",
            "relevance_0_2",
            "faithfulness_0_2",
            "notes",
        ],
    )
    writer.writeheader()
    for result in results:
        writer.writerow(
            {
                "case_id": result.case_id,
                "category": result.category,
                "relevance_0_2": "",
                "faithfulness_0_2": "",
                "notes": "",
            }
        )
    return output.getvalue()


def load_human_reviews(content: str) -> list[GenerationJudgeHumanReview]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise ValueError("人工评分 CSV 缺少表头")
    missing_columns = _REQUIRED_COLUMNS.difference(reader.fieldnames)
    if missing_columns:
        columns = ", ".join(sorted(missing_columns))
        raise ValueError(f"人工评分 CSV 缺少列：{columns}")

    reviews: list[GenerationJudgeHumanReview] = []
    seen_case_ids: set[str] = set()
    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue
        case_id = (row.get("case_id") or "").strip()
        if not case_id:
            raise ValueError(f"人工评分 CSV 第 {line_number} 行缺少 case_id")
        if case_id in seen_case_ids:
            raise ValueError(f"人工评分 CSV 中 case_id 重复：{case_id}")
        seen_case_ids.add(case_id)
        reviews.append(
            GenerationJudgeHumanReview(
                case_id=case_id,
                relevance_0_2=_parse_score(
                    row.get("relevance_0_2"),
                    field="relevance_0_2",
                    line_number=line_number,
                ),
                faithfulness_0_2=_parse_score(
                    row.get("faithfulness_0_2"),
                    field="faithfulness_0_2",
                    line_number=line_number,
                ),
                notes=(row.get("notes") or "").strip(),
            )
        )
    if not reviews:
        raise ValueError("人工评分 CSV 没有已填写的样本")
    return reviews


def calibrate_generation_judge(
    report: GenerationJudgeReport,
    human_reviews: Sequence[GenerationJudgeHumanReview],
) -> GenerationJudgeCalibrationReport:
    if not human_reviews:
        raise ValueError("人工评分不能为空")
    seen_review_ids: set[str] = set()
    duplicate_review_ids: set[str] = set()
    for review in human_reviews:
        if review.case_id in seen_review_ids:
            duplicate_review_ids.add(review.case_id)
        seen_review_ids.add(review.case_id)
    if duplicate_review_ids:
        raise ValueError(
            "人工评分中存在重复 case_id："
            + ", ".join(sorted(duplicate_review_ids))
        )

    judged_results = _judged_results(report)
    judged_by_id = {result.case_id: result for result in judged_results}
    reviews_by_id = {review.case_id: review for review in human_reviews}
    unknown_case_ids = sorted(set(reviews_by_id).difference(judged_by_id))
    if unknown_case_ids:
        raise ValueError(
            "人工评分包含非 judge 成功样本："
            + ", ".join(unknown_case_ids)
        )

    results: list[GenerationJudgeCalibrationCase] = []
    for judged in judged_results:
        review = reviews_by_id.get(judged.case_id)
        if review is None or judged.assessment is None:
            continue
        judge_relevance_score = _RELEVANCE_SCORES[judged.assessment.relevance]
        judge_faithfulness_score = _FAITHFULNESS_SCORES[
            judged.assessment.faithfulness
        ]
        human_strict_pass = (
            review.relevance_0_2 == 2
            and review.faithfulness_0_2 == 2
        )
        judge_strict_pass = (
            judge_relevance_score == 2
            and judge_faithfulness_score == 2
        )
        results.append(
            GenerationJudgeCalibrationCase(
                case_id=judged.case_id,
                category=judged.category,
                human_relevance_0_2=review.relevance_0_2,
                judge_relevance=judged.assessment.relevance,
                judge_relevance_0_2=judge_relevance_score,
                relevance_exact_match=(
                    review.relevance_0_2 == judge_relevance_score
                ),
                human_faithfulness_0_2=review.faithfulness_0_2,
                judge_faithfulness=judged.assessment.faithfulness,
                judge_faithfulness_0_2=judge_faithfulness_score,
                faithfulness_exact_match=(
                    review.faithfulness_0_2 == judge_faithfulness_score
                ),
                human_strict_pass=human_strict_pass,
                judge_strict_pass=judge_strict_pass,
                strict_pass_match=human_strict_pass == judge_strict_pass,
                strict_pass_direction=_strict_pass_direction(
                    human_strict_pass=human_strict_pass,
                    judge_strict_pass=judge_strict_pass,
                ),
                notes=review.notes,
            )
        )

    return GenerationJudgeCalibrationReport(
        source_dataset_version=report.source_dataset_version,
        source_generated_at=report.source_generated_at,
        generator_model=report.generator_model,
        judge_model=report.judge_model,
        reviewed_at=datetime.now(UTC),
        summary=_summarize(
            eligible_cases=len(judged_results),
            results=results,
        ),
        results=results,
    )


def format_calibration_summary(
    report: GenerationJudgeCalibrationReport,
) -> str:
    summary = report.summary
    lines = [
        f"source_dataset={report.source_dataset_version}",
        f"generator_model={report.generator_model} judge_model={report.judge_model}",
        (
            f"reviewed={summary.reviewed_cases}/{summary.eligible_cases} "
            f"coverage={summary.coverage_rate:.6f}"
        ),
        (
            "relevance_exact_agreement="
            f"{summary.relevance_exact_agreement_rate:.6f} "
            "faithfulness_exact_agreement="
            f"{summary.faithfulness_exact_agreement_rate:.6f}"
        ),
        (
            f"strict_pass_agreement={summary.strict_pass_agreement_rate:.6f} "
            f"judge_stricter={summary.judge_stricter_cases} "
            f"judge_looser={summary.judge_looser_cases}"
        ),
    ]
    directions = [
        result
        for result in report.results
        if result.strict_pass_direction != "match"
    ]
    if directions:
        lines.extend(["", "strict_pass_disagreements:"])
        lines.extend(
            f"- {result.case_id}: {result.strict_pass_direction}"
            for result in directions
        )
    return "\n".join(lines)


def _judged_results(
    report: GenerationJudgeReport,
    *,
    case_ids: set[str] | None = None,
) -> list[GenerationJudgeCaseResult]:
    return [
        result
        for result in report.results
        if result.judge_status == "judged"
        and result.assessment is not None
        and (case_ids is None or result.case_id in case_ids)
    ]


def _parse_score(
    value: str | None,
    *,
    field: str,
    line_number: int,
) -> int:
    normalized = (value or "").strip()
    if normalized not in {"0", "1", "2"}:
        raise ValueError(
            f"人工评分 CSV 第 {line_number} 行 {field} 必须为 0、1 或 2"
        )
    return int(normalized)


def _strict_pass_direction(
    *,
    human_strict_pass: bool,
    judge_strict_pass: bool,
) -> CalibrationDirection:
    if human_strict_pass == judge_strict_pass:
        return "match"
    if human_strict_pass:
        return "judge_stricter"
    return "judge_looser"


def _summarize(
    *,
    eligible_cases: int,
    results: list[GenerationJudgeCalibrationCase],
) -> GenerationJudgeCalibrationSummary:
    reviewed_cases = len(results)
    return GenerationJudgeCalibrationSummary(
        eligible_cases=eligible_cases,
        reviewed_cases=reviewed_cases,
        coverage_rate=_rounded_ratio(reviewed_cases, eligible_cases),
        relevance_exact_agreement_rate=_rounded_ratio(
            sum(result.relevance_exact_match for result in results),
            reviewed_cases,
        ),
        faithfulness_exact_agreement_rate=_rounded_ratio(
            sum(result.faithfulness_exact_match for result in results),
            reviewed_cases,
        ),
        strict_pass_agreement_rate=_rounded_ratio(
            sum(result.strict_pass_match for result in results),
            reviewed_cases,
        ),
        judge_stricter_cases=sum(
            result.strict_pass_direction == "judge_stricter"
            for result in results
        ),
        judge_looser_cases=sum(
            result.strict_pass_direction == "judge_looser"
            for result in results
        ),
    )


def _rounded_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0
