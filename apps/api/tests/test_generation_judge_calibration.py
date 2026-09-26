from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.evaluation.generation_judge_calibration import (
    calibrate_generation_judge,
    export_calibration_template,
    format_calibration_summary,
    load_human_reviews,
)
from app.schemas.generation_judge import (
    ClaimSupport,
    FaithfulnessVerdict,
    GenerationJudgeAssessment,
    GenerationJudgeCaseResult,
    GenerationJudgeClaim,
    GenerationJudgeReport,
    GenerationJudgeSummary,
    RelevanceVerdict,
)
from app.schemas.generation_judge_calibration import (
    GenerationJudgeHumanReview,
)

CALIBRATION_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "calibrate_generation_judge.py"
)


def _assessment(
    *,
    relevance: RelevanceVerdict = "relevant",
    supports: Sequence[ClaimSupport] = ("supported",),
) -> GenerationJudgeAssessment:
    claims = [
        GenerationJudgeClaim(
            text=f"事实声明 {index}",
            citation_ranks=[1] if support == "supported" else [],
            support=support,
        )
        for index, support in enumerate(supports, start=1)
    ]
    statuses = set(supports)
    if statuses == {"supported"}:
        faithfulness: FaithfulnessVerdict = "supported"
    elif statuses == {"unsupported"}:
        faithfulness = "unsupported"
    else:
        faithfulness = "partially_supported"
    return GenerationJudgeAssessment(
        relevance=relevance,
        faithfulness=faithfulness,
        claims=claims,
        reason="测试判定。",
    )


_DEFAULT_ASSESSMENT = _assessment()


def _case(
    case_id: str,
    *,
    assessment: GenerationJudgeAssessment | None = _DEFAULT_ASSESSMENT,
    judge_status: str = "judged",
) -> GenerationJudgeCaseResult:
    return GenerationJudgeCaseResult(
        case_id=case_id,
        category="answer",
        question=f"{case_id} 的问题",
        expected="answer",
        deterministic_passed=True,
        deterministic_answer_correct=True,
        refused=False,
        answer=f"{case_id} 的回答。[1]",
        citation_count=1,
        judge_status=judge_status,
        assessment=assessment,
        error_code=None,
        latency_ms=10.0,
        citations=[],
    )


def _report(
    results: list[GenerationJudgeCaseResult],
    *,
    judge_failure_case_ids: list[str] | None = None,
) -> GenerationJudgeReport:
    judged_count = sum(
        result.judge_status == "judged" and result.assessment is not None
        for result in results
    )
    return GenerationJudgeReport(
        source_dataset_version="generation-calibration-test-v1",
        source_generated_at=datetime.now(UTC),
        generator_model="fake-generator",
        judge_model="fake-judge",
        generated_at=datetime.now(UTC),
        summary=GenerationJudgeSummary(
            total_cases=len(results),
            answerable_cases=len(results),
            refusal_cases=0,
            judged_cases=judged_count,
            judge_error_cases=sum(
                result.judge_status == "judge_error" for result in results
            ),
            deterministic_pass_rate=1.0,
            faithfulness_pass_rate=0.0,
            answer_relevance_rate=1.0,
            claim_support_rate=0.0,
            average_unsupported_claims=0.0,
            refusal_accuracy=None,
        ),
        categories={},
        deterministic_failure_case_ids=[],
        judge_failure_case_ids=(
            judge_failure_case_ids
            if judge_failure_case_ids is not None
            else []
        ),
        results=results,
    )


def _load_calibration_cli():
    spec = importlib.util.spec_from_file_location(
        "calibrate_generation_judge_script",
        CALIBRATION_SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_template_only_includes_judged_successes() -> None:
    report = _report(
        [
            _case("judged-good"),
            _case(
                "skipped-refusal",
                assessment=None,
                judge_status="skipped_refusal",
            ),
            _case(
                "judge-error",
                assessment=None,
                judge_status="judge_error",
            ),
        ]
    )

    content = export_calibration_template(report)

    assert "case_id,category,relevance_0_2,faithfulness_0_2,notes" in content
    assert "judged-good" in content
    assert "skipped-refusal" not in content
    assert "judge-error" not in content


def test_export_template_respects_case_ids() -> None:
    report = _report(
        [
            _case("case-a"),
            _case("case-b"),
            _case("case-c"),
        ]
    )

    content = export_calibration_template(
        report,
        case_ids={"case-a", "case-c"},
    )

    assert "case-a" in content
    assert "case-c" in content
    assert "case-b" not in content


def test_load_human_reviews_accepts_bom_blank_lines_and_notes() -> None:
    content = (
        "\ufeffcase_id,category,relevance_0_2,faithfulness_0_2,notes\n"
        "\n"
        'case-a,answer,2,1,"相关，但解释超出引用"\n'
        "case-b,answer,1,2,\n"
    )

    reviews = load_human_reviews(content)

    assert reviews == [
        GenerationJudgeHumanReview(
            case_id="case-a",
            relevance_0_2=2,
            faithfulness_0_2=1,
            notes="相关，但解释超出引用",
        ),
        GenerationJudgeHumanReview(
            case_id="case-b",
            relevance_0_2=1,
            faithfulness_0_2=2,
            notes="",
        ),
    ]


@pytest.mark.parametrize("score", ["", "3", "high"])
def test_load_human_reviews_rejects_invalid_scores(score: str) -> None:
    content = (
        "case_id,relevance_0_2,faithfulness_0_2,notes\n"
        f"case-a,{score},2,\n"
    )

    with pytest.raises(ValueError, match="必须为 0、1 或 2"):
        load_human_reviews(content)


def test_load_human_reviews_rejects_duplicate_case_ids() -> None:
    content = (
        "case_id,relevance_0_2,faithfulness_0_2,notes\n"
        "case-a,2,2,\n"
        "case-a,1,1,\n"
    )

    with pytest.raises(ValueError, match="case_id 重复"):
        load_human_reviews(content)


def test_calibration_reports_metrics_and_disagreement_directions() -> None:
    report = _report(
        [
            _case("case-match"),
            _case(
                "case-dimension-mismatch",
                assessment=_assessment(supports=("supported", "unsupported")),
            ),
            _case(
                "case-judge-stricter",
                assessment=_assessment(supports=("supported", "unsupported")),
            ),
            _case("case-judge-looser"),
        ]
    )
    reviews = [
        GenerationJudgeHumanReview(
            case_id="case-match",
            relevance_0_2=2,
            faithfulness_0_2=2,
        ),
        GenerationJudgeHumanReview(
            case_id="case-dimension-mismatch",
            relevance_0_2=1,
            faithfulness_0_2=1,
        ),
        GenerationJudgeHumanReview(
            case_id="case-judge-stricter",
            relevance_0_2=2,
            faithfulness_0_2=2,
        ),
        GenerationJudgeHumanReview(
            case_id="case-judge-looser",
            relevance_0_2=2,
            faithfulness_0_2=1,
        ),
    ]

    calibration = calibrate_generation_judge(report, reviews)

    assert calibration.summary.eligible_cases == 4
    assert calibration.summary.reviewed_cases == 4
    assert calibration.summary.coverage_rate == 1.0
    assert calibration.summary.relevance_exact_agreement_rate == 0.75
    assert calibration.summary.faithfulness_exact_agreement_rate == 0.5
    assert calibration.summary.strict_pass_agreement_rate == 0.5
    assert calibration.summary.judge_stricter_cases == 1
    assert calibration.summary.judge_looser_cases == 1
    assert [
        result.strict_pass_direction for result in calibration.results
    ] == ["match", "match", "judge_stricter", "judge_looser"]

    summary = format_calibration_summary(calibration)

    assert "strict_pass_agreement=0.500000" in summary
    assert "- case-judge-stricter: judge_stricter" in summary
    assert "- case-judge-looser: judge_looser" in summary


def test_calibration_allows_partial_review_coverage() -> None:
    report = _report([_case("case-a"), _case("case-b")])
    reviews = [
        GenerationJudgeHumanReview(
            case_id="case-a",
            relevance_0_2=2,
            faithfulness_0_2=2,
        )
    ]

    calibration = calibrate_generation_judge(report, reviews)

    assert calibration.summary.eligible_cases == 2
    assert calibration.summary.reviewed_cases == 1
    assert calibration.summary.coverage_rate == 0.5
    assert [result.case_id for result in calibration.results] == ["case-a"]


def test_calibration_rejects_empty_duplicate_and_unknown_reviews() -> None:
    report = _report([_case("case-a")])
    review = GenerationJudgeHumanReview(
        case_id="case-a",
        relevance_0_2=2,
        faithfulness_0_2=2,
    )

    with pytest.raises(ValueError, match="人工评分不能为空"):
        calibrate_generation_judge(report, [])
    with pytest.raises(ValueError, match="重复 case_id"):
        calibrate_generation_judge(report, [review, review])
    with pytest.raises(ValueError, match="非 judge 成功样本"):
        calibrate_generation_judge(
            report,
            [
                GenerationJudgeHumanReview(
                    case_id="unknown",
                    relevance_0_2=2,
                    faithfulness_0_2=2,
                )
            ],
        )


def test_calibration_cli_exports_template_and_review_without_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _report(
        [
            _case("case-flagged"),
            _case("case-other"),
            _case(
                "case-error",
                assessment=None,
                judge_status="judge_error",
            ),
        ],
        judge_failure_case_ids=["case-flagged", "case-error"],
    )
    report_path = tmp_path / "judge.json"
    template_path = tmp_path / "calibration.csv"
    review_path = tmp_path / "review.md"
    report_path.write_text(report.model_dump_json(), encoding="utf-8")
    module = _load_calibration_cli()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "calibrate_generation_judge.py",
            "--judge-report",
            str(report_path),
            "--export-template",
            str(template_path),
            "--review-output",
            str(review_path),
        ],
    )

    module.main()

    assert "case-flagged" in template_path.read_text(encoding="utf-8")
    assert "case-other" not in template_path.read_text(encoding="utf-8")
    assert "case-error" not in template_path.read_text(encoding="utf-8")
    assert "case-flagged" in review_path.read_text(encoding="utf-8")
    assert "case-other" not in review_path.read_text(encoding="utf-8")
    assert "scope=flagged cases=1" in capsys.readouterr().out
