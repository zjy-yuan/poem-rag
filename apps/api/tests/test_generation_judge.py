from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from app.ai.providers.chat import (
    ChatMessage,
    ChatModelError,
    ChatResponseFormat,
)
from app.evaluation.generation_judge import (
    GenerationJudge,
    export_blind_review_markdown,
)
from app.schemas.generation_evaluation import (
    GenerationCitationSnapshot,
    GenerationEvaluationCaseResult,
    GenerationEvaluationReport,
    GenerationEvaluationSummary,
)
from app.schemas.generation_judge import GenerationJudgeAssessment
from pydantic import ValidationError


class FakeJudgeProvider:
    model = "fake-judge"

    def __init__(self, responses: Sequence[str | Exception]) -> None:
        self.responses = list(responses)
        self.messages: list[list[ChatMessage]] = []

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int,
        temperature: float = 0.0,
        response_format: ChatResponseFormat | None = None,
    ) -> str:
        del max_output_tokens, temperature, response_format
        self.messages.append(list(messages))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _citation(*, rank: int = 1) -> GenerationCitationSnapshot:
    return GenerationCitationSnapshot(
        chunk_id=1,
        poem_id=1,
        poem_version_id=1,
        annotation_id=None,
        title="静夜思",
        author_name="李白",
        dynasty_name="唐",
        granularity="line",
        text="举头望明月，低头思故乡。",
        score=1.0,
        rank=rank,
    )


def _case_result(
    *,
    case_id: str,
    expected: str,
    answer: str,
    passed: bool,
    answer_correct: bool,
    refused: bool,
    citations: list[GenerationCitationSnapshot] | None = None,
    error_code: str | None = None,
) -> GenerationEvaluationCaseResult:
    citation_items = citations or []
    return GenerationEvaluationCaseResult(
        case_id=case_id,
        category="answer",
        question="《静夜思》如何表达思乡？",
        expected=expected,
        passed=passed,
        answer_correct=answer_correct,
        refused=refused,
        answer=answer,
        finish_reason="no_evidence" if refused else "stop",
        assessment_status="refused" if refused else "passed",
        assessment_reason_code="missing_fact" if refused else "supported",
        error_code=error_code,
        required_fact_matches={},
        required_facts_missing=[],
        forbidden_facts_present=[],
        citation_count=len(citation_items),
        matched_citation_ranks=[citation.rank for citation in citation_items],
        matched_expected_citation_indexes=[1] if citation_items else [],
        missing_expected_citation_indexes=[],
        citation_precision=1.0 if citation_items else None,
        citation_recall=1.0 if citation_items else None,
        retrieval_candidate_count=3,
        retrieval_selected_count=1,
        strategy="test-strategy",
        latency_ms=10.0,
        citations=citation_items,
    )


def _report(
    results: list[GenerationEvaluationCaseResult],
    *,
    failure_case_ids: list[str] | None = None,
) -> GenerationEvaluationReport:
    return GenerationEvaluationReport(
        dataset_version="generation-test-v1",
        model="fake-generator",
        strategy="test-strategy",
        generated_at=datetime.now(UTC),
        summary=GenerationEvaluationSummary(
            total_cases=len(results),
            answerable_cases=sum(result.expected == "answer" for result in results),
            refusal_cases=sum(result.expected == "refusal" for result in results),
            passed_cases=sum(result.passed for result in results),
            pass_rate=0.0,
            answer_accuracy=0.0,
            refusal_accuracy=0.0,
            refusal_precision=0.0,
            refusal_recall=0.0,
            refusal_f1=0.0,
            citation_precision=0.0,
            citation_recall=0.0,
            average_latency_ms=10.0,
            p95_latency_ms=10.0,
        ),
        categories={},
        failure_case_ids=(
            failure_case_ids
            if failure_case_ids is not None
            else [result.case_id for result in results if not result.passed]
        ),
        results=results,
    )


def _assessment_payload(
    *,
    relevance: str = "relevant",
    faithfulness: str = "supported",
    claims: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {
            "relevance": relevance,
            "faithfulness": faithfulness,
            "claims": claims
            or [
                {
                    "text": "明月常被用来表达思乡。",
                    "citation_ranks": [1],
                    "support": "supported",
                }
            ],
            "reason": "回答与所引诗句一致。",
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_generation_judge_scores_answer_cases_and_skips_refusals() -> None:
    provider = FakeJudgeProvider([_assessment_payload()])
    source = _report(
        [
            _case_result(
                case_id="answer-good",
                expected="answer",
                answer="明月常用来表达思乡。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            ),
            _case_result(
                case_id="answer-refused",
                expected="answer",
                answer="现有证据不足。",
                passed=False,
                answer_correct=False,
                refused=True,
            ),
            _case_result(
                case_id="refusal-good",
                expected="refusal",
                answer="现有证据不足。",
                passed=True,
                answer_correct=True,
                refused=True,
            ),
        ]
    )

    report = await GenerationJudge(provider).evaluate(source)

    assert report.judge_model == "fake-judge"
    assert report.summary.total_cases == 3
    assert report.summary.judged_cases == 1
    assert report.summary.judge_error_cases == 0
    assert report.summary.faithfulness_pass_rate == 1.0
    assert report.summary.answer_relevance_rate == 1.0
    assert report.summary.claim_support_rate == 1.0
    assert report.summary.average_unsupported_claims == 0.0
    assert report.summary.refusal_accuracy == 1.0
    assert report.deterministic_failure_case_ids == ["answer-refused"]
    assert report.judge_failure_case_ids == []
    assert report.results[1].judge_status == "skipped_refusal"
    assert report.results[2].judge_status == "skipped_refusal"

    messages = provider.messages[0]
    assert messages[0].role == "system"
    assert "只输出 JSON 对象" in messages[0].content
    assert "举头望明月，低头思故乡。" in messages[1].content
    assert '"question": "《静夜思》如何表达思乡？"' in messages[1].content


@pytest.mark.asyncio
async def test_generation_judge_records_partial_faithfulness() -> None:
    provider = FakeJudgeProvider(
        [
            _assessment_payload(
                faithfulness="partially_supported",
                claims=[
                    {
                        "text": "明月表达思乡。",
                        "citation_ranks": [1],
                        "support": "supported",
                    },
                    {
                        "text": "诗人当时正在长安。",
                        "citation_ranks": [],
                        "support": "unsupported",
                    },
                ],
            )
        ]
    )
    source = _report(
        [
            _case_result(
                case_id="answer-partial",
                expected="answer",
                answer="明月表达思乡，诗人当时正在长安。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            )
        ]
    )

    report = await GenerationJudge(provider).evaluate(source)

    assert report.summary.faithfulness_pass_rate == 0.0
    assert report.summary.claim_support_rate == 0.5
    assert report.summary.average_unsupported_claims == 1.0
    assert report.judge_failure_case_ids == ["answer-partial"]


@pytest.mark.asyncio
async def test_generation_judge_derives_faithfulness_from_claims() -> None:
    provider = FakeJudgeProvider(
        [
            _assessment_payload(
                faithfulness="supported",
                claims=[
                    {
                        "text": "明月表达思乡。",
                        "citation_ranks": [1],
                        "support": "supported",
                    },
                    {
                        "text": "诗人当时正在长安。",
                        "citation_ranks": [],
                        "support": "unsupported",
                    },
                ],
            )
        ]
    )
    source = _report(
        [
            _case_result(
                case_id="answer-derived-faithfulness",
                expected="answer",
                answer="明月表达思乡，诗人当时正在长安。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            )
        ]
    )

    report = await GenerationJudge(provider).evaluate(source)

    assert report.results[0].judge_status == "judged"
    assert report.results[0].assessment is not None
    assert report.results[0].assessment.faithfulness == "partially_supported"


@pytest.mark.asyncio
async def test_generation_judge_isolates_invalid_responses_and_rank_errors() -> None:
    provider = FakeJudgeProvider(
        [
            "not-json",
            _assessment_payload(
                claims=[
                    {
                        "text": "引用了不存在的证据。",
                        "citation_ranks": [2],
                        "support": "supported",
                    }
                ]
            ),
            _assessment_payload(),
        ]
    )
    source = _report(
        [
            _case_result(
                case_id="answer-invalid-json",
                expected="answer",
                answer="回答一。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            ),
            _case_result(
                case_id="answer-invalid-rank",
                expected="answer",
                answer="回答二。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            ),
            _case_result(
                case_id="answer-good-after-errors",
                expected="answer",
                answer="回答三。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            ),
        ]
    )

    report = await GenerationJudge(provider).evaluate(source)

    assert report.summary.judged_cases == 1
    assert report.summary.judge_error_cases == 2
    assert report.results[0].error_code == "INVALID_JUDGE_RESPONSE"
    assert report.results[1].error_code == "INVALID_JUDGE_RESPONSE"
    assert report.results[2].judge_status == "judged"
    assert report.judge_failure_case_ids == [
        "answer-invalid-json",
        "answer-invalid-rank",
    ]


@pytest.mark.asyncio
async def test_generation_judge_accepts_sparse_citation_ranks() -> None:
    provider = FakeJudgeProvider(
        [
            _assessment_payload(
                claims=[
                    {
                        "text": "第三段证据支持该结论。",
                        "citation_ranks": [3],
                        "support": "supported",
                    }
                ]
            )
        ]
    )
    source = _report(
        [
            _case_result(
                case_id="answer-sparse-ranks",
                expected="answer",
                answer="结论。[3]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation(rank=1), _citation(rank=3)],
            )
        ]
    )

    report = await GenerationJudge(provider).evaluate(source)

    assert report.results[0].judge_status == "judged"
    assert report.summary.judge_error_cases == 0


@pytest.mark.asyncio
async def test_generation_judge_isolates_provider_errors() -> None:
    provider = FakeJudgeProvider(
        [
            ChatModelError("judge timeout", code="MODEL_TIMEOUT"),
            _assessment_payload(),
        ]
    )
    source = _report(
        [
            _case_result(
                case_id="answer-provider-error",
                expected="answer",
                answer="回答一。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            ),
            _case_result(
                case_id="answer-after-error",
                expected="answer",
                answer="回答二。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            ),
        ]
    )

    report = await GenerationJudge(provider).evaluate(source)

    assert report.results[0].error_code == "MODEL_TIMEOUT"
    assert report.results[0].judge_status == "judge_error"
    assert report.results[1].judge_status == "judged"
    assert report.summary.judge_error_cases == 1


def test_generation_judge_rejects_inconsistent_faithfulness() -> None:
    with pytest.raises(ValidationError, match="faithfulness 与事实声明"):
        GenerationJudgeAssessment.model_validate(
            {
                "relevance": "relevant",
                "faithfulness": "supported",
                "claims": [
                    {
                        "text": "无依据事实。",
                        "citation_ranks": [],
                        "support": "unsupported",
                    }
                ],
                "reason": "不一致。",
            }
        )


@pytest.mark.asyncio
async def test_export_blind_review_omits_automatic_scores() -> None:
    provider = FakeJudgeProvider([_assessment_payload()])
    source = _report(
        [
            _case_result(
                case_id="answer-review",
                expected="answer",
                answer="明月常用来表达思乡。[1]",
                passed=True,
                answer_correct=True,
                refused=False,
                citations=[_citation()],
            )
        ]
    )
    report = await GenerationJudge(provider).evaluate(source)

    markdown = export_blind_review_markdown(report)

    assert "明月常用来表达思乡。[1]" in markdown
    assert "《静夜思》" in markdown
    assert "faithfulness" not in markdown
    assert "相关性（0/1/2）" in markdown
