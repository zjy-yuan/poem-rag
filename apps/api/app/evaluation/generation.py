from __future__ import annotations

import json
import math
import statistics
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from app.ai.graphs.rag import NO_EVIDENCE_ANSWER, CitationDraft
from app.ai.providers.chat import ChatMessage, ChatModelError
from app.core.text import normalize_content, normalize_lookup
from app.schemas.evaluation import EvidenceSelector
from app.schemas.generation_evaluation import (
    FactRequirement,
    GenerationCitationSnapshot,
    GenerationEvaluationCase,
    GenerationEvaluationCaseResult,
    GenerationEvaluationDataset,
    GenerationEvaluationReport,
    GenerationEvaluationSummary,
)


class GenerationGraphPort(Protocol):
    def stream(
        self,
        *,
        query: str,
        history: Sequence[ChatMessage],
    ) -> AsyncIterator[dict[str, Any]]: ...


class GenerationGraphFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[GenerationGraphPort]: ...


def load_generation_evaluation_dataset(
    path: Path,
) -> GenerationEvaluationDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return GenerationEvaluationDataset.model_validate(payload)


def matches_citation_selector(
    citation: CitationDraft,
    selector: EvidenceSelector,
) -> bool:
    if selector.poem_title is not None and normalize_lookup(
        citation.title
    ) != normalize_lookup(selector.poem_title):
        return False
    if selector.author_name is not None and (
        citation.author_name is None
        or normalize_lookup(citation.author_name)
        != normalize_lookup(selector.author_name)
    ):
        return False
    if selector.dynasty_name is not None and (
        citation.dynasty_name is None
        or normalize_lookup(citation.dynasty_name)
        != normalize_lookup(selector.dynasty_name)
    ):
        return False
    if (
        selector.granularity is not None
        and citation.granularity != selector.granularity.value
    ):
        return False
    if selector.text_contains is not None and normalize_content(
        selector.text_contains
    ) not in normalize_content(citation.text):
        return False
    return True


class GenerationEvaluator:
    def __init__(
        self,
        graph_factory: GenerationGraphFactory,
        *,
        model: str,
    ) -> None:
        self.graph_factory = graph_factory
        self.model = model

    async def evaluate(
        self,
        dataset: GenerationEvaluationDataset,
    ) -> GenerationEvaluationReport:
        results = [
            await self._evaluate_case(case)
            for case in dataset.cases
        ]
        categories = {
            category: _summarize(
                [result for result in results if result.category == category]
            )
            for category in dict.fromkeys(result.category for result in results)
        }
        return GenerationEvaluationReport(
            dataset_version=dataset.version,
            model=self.model,
            strategy=_summarize_strategy(results),
            generated_at=datetime.now(UTC),
            summary=_summarize(results),
            categories=categories,
            failure_case_ids=[result.case_id for result in results if not result.passed],
            results=results,
        )

    async def _evaluate_case(
        self,
        case: GenerationEvaluationCase,
    ) -> GenerationEvaluationCaseResult:
        started_at = perf_counter()
        answer_parts: list[str] = []
        citations: list[CitationDraft] = []
        final_answer: str | None = None
        finish_reason: str | None = None
        assessment_status: str | None = None
        assessment_reason_code: str | None = None
        error_code: str | None = None
        strategy: str | None = None
        candidate_count = 0
        selected_count = 0

        try:
            async with self.graph_factory() as graph:
                async for event in graph.stream(query=case.question, history=[]):
                    kind = event.get("kind")
                    if kind == "retrieval":
                        candidate_count = _safe_int(event.get("candidate_count"))
                        selected_count = _safe_int(event.get("selected_count"))
                        raw_strategy = event.get("strategy")
                        if isinstance(raw_strategy, str) and raw_strategy:
                            strategy = raw_strategy
                    elif kind == "delta":
                        text = event.get("text")
                        if isinstance(text, str) and text:
                            answer_parts.append(text)
                    elif kind == "citation":
                        citation = event.get("citation")
                        if isinstance(citation, CitationDraft):
                            citations.append(citation)
                    elif kind == "assessment":
                        raw_status = event.get("status")
                        if isinstance(raw_status, str) and raw_status:
                            assessment_status = raw_status
                        raw_reason_code = event.get("reason_code")
                        if isinstance(raw_reason_code, str) and raw_reason_code:
                            assessment_reason_code = raw_reason_code
                    elif kind == "final":
                        raw_answer = event.get("answer")
                        if isinstance(raw_answer, str):
                            final_answer = raw_answer
                        raw_citations = event.get("citations")
                        if isinstance(raw_citations, list):
                            citations = [
                                item
                                for item in raw_citations
                                if isinstance(item, CitationDraft)
                            ]
                        raw_finish_reason = event.get("finish_reason")
                        if isinstance(raw_finish_reason, str):
                            finish_reason = raw_finish_reason
        except ChatModelError as exc:
            error_code = str(exc.code)
        except Exception as exc:
            error_code = type(exc).__name__

        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        answer = final_answer if final_answer is not None else "".join(answer_parts)
        refused = finish_reason == "no_evidence" or answer.strip() == NO_EVIDENCE_ANSWER
        return _score_case(
            case,
            answer=answer,
            citations=citations,
            refused=refused,
            finish_reason=finish_reason,
            assessment_status=assessment_status,
            assessment_reason_code=assessment_reason_code,
            error_code=error_code,
            candidate_count=candidate_count,
            selected_count=selected_count,
            strategy=strategy,
            latency_ms=latency_ms,
        )


def format_generation_evaluation_report(
    report: GenerationEvaluationReport,
) -> str:
    summary = report.summary
    lines = [
        f"dataset={report.dataset_version}",
        f"model={report.model} strategy={report.strategy or 'unknown'}",
        (
            f"cases={summary.total_cases} passed={summary.passed_cases} "
            f"pass_rate={_format_optional(summary.pass_rate)}"
        ),
        (
            f"answer_accuracy={_format_optional(summary.answer_accuracy)} "
            f"refusal_accuracy={_format_optional(summary.refusal_accuracy)}"
        ),
        (
            f"refusal_precision={_format_optional(summary.refusal_precision)} "
            f"refusal_recall={_format_optional(summary.refusal_recall)} "
            f"refusal_f1={_format_optional(summary.refusal_f1)}"
        ),
        (
            f"citation_precision={_format_optional(summary.citation_precision)} "
            f"citation_recall={_format_optional(summary.citation_recall)}"
        ),
        (
            f"average_latency_ms={summary.average_latency_ms:.3f} "
            f"p95_latency_ms={summary.p95_latency_ms:.3f}"
        ),
        "",
        "categories:",
    ]
    for category, category_summary in report.categories.items():
        lines.append(
            f"- {category}: cases={category_summary.total_cases} "
            f"pass_rate={_format_optional(category_summary.pass_rate)} "
            f"answer_accuracy={_format_optional(category_summary.answer_accuracy)} "
            f"refusal_f1={_format_optional(category_summary.refusal_f1)}"
        )
    failures = [result for result in report.results if not result.passed]
    if failures:
        lines.extend(["", "failures:"])
        for result in failures:
            lines.append(
                f"- {result.case_id}: expected={result.expected} "
                f"refused={result.refused} "
                f"assessment={result.assessment_status or 'n/a'}"
                f"/{result.assessment_reason_code or 'n/a'} "
                f"retrieval={result.retrieval_selected_count}"
                f"/{result.retrieval_candidate_count} "
                f"citations={result.citation_count} "
                f"error={result.error_code or 'none'}"
            )
    return "\n".join(lines)


def _score_case(
    case: GenerationEvaluationCase,
    *,
    answer: str,
    citations: list[CitationDraft],
    refused: bool,
    finish_reason: str | None,
    assessment_status: str | None,
    assessment_reason_code: str | None,
    error_code: str | None,
    candidate_count: int,
    selected_count: int,
    strategy: str | None,
    latency_ms: float,
) -> GenerationEvaluationCaseResult:
    normalized_answer = normalize_content(answer)
    required_matches = _match_required_facts(
        case.required_facts,
        normalized_answer,
    )
    required_missing = [
        fact.id
        for fact in case.required_facts
        if fact.id not in required_matches
    ]
    forbidden_present = _match_forbidden_facts(
        case.forbidden_facts,
        normalized_answer,
    )

    matched_citation_ranks = [
        citation.rank
        for citation in citations
        if any(
            matches_citation_selector(citation, selector)
            for selector in case.expected_citations
        )
    ]
    matched_expected_indexes = [
        index
        for index, selector in enumerate(case.expected_citations, start=1)
        if any(
            matches_citation_selector(citation, selector)
            for citation in citations
        )
    ]
    missing_expected_indexes = [
        index
        for index in range(1, len(case.expected_citations) + 1)
        if index not in matched_expected_indexes
    ]
    citation_precision = (
        _rounded_ratio(len(matched_citation_ranks), len(citations))
        if citations
        else None
    )
    citation_recall = (
        _rounded_ratio(
            len(matched_expected_indexes),
            len(case.expected_citations),
        )
        if case.expected_citations
        else None
    )

    if case.expected == "answer":
        answer_correct = (
            error_code is None
            and not refused
            and not required_missing
            and not forbidden_present
        )
        passed = answer_correct and citation_recall == 1.0
    else:
        answer_correct = error_code is None and refused
        passed = answer_correct and not citations

    return GenerationEvaluationCaseResult(
        case_id=case.id,
        category=case.category,
        question=case.question,
        expected=case.expected,
        passed=passed,
        answer_correct=answer_correct,
        refused=refused,
        answer=answer,
        finish_reason=finish_reason,
        assessment_status=assessment_status,
        assessment_reason_code=assessment_reason_code,
        error_code=error_code,
        required_fact_matches=required_matches,
        required_facts_missing=required_missing,
        forbidden_facts_present=forbidden_present,
        citation_count=len(citations),
        matched_citation_ranks=matched_citation_ranks,
        matched_expected_citation_indexes=matched_expected_indexes,
        missing_expected_citation_indexes=missing_expected_indexes,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        retrieval_candidate_count=candidate_count,
        retrieval_selected_count=selected_count,
        strategy=strategy,
        latency_ms=latency_ms,
        citations=[
            _snapshot_citation(citation)
            for citation in citations
        ],
    )


def _match_required_facts(
    requirements: list[FactRequirement],
    normalized_answer: str,
) -> dict[str, str]:
    matches: dict[str, str] = {}
    for requirement in requirements:
        matched_term = next(
            (
                term
                for term in requirement.any_of
                if normalize_content(term) in normalized_answer
            ),
            None,
        )
        if matched_term is not None:
            matches[requirement.id] = matched_term
    return matches


def _match_forbidden_facts(
    requirements: list[FactRequirement],
    normalized_answer: str,
) -> list[str]:
    return [
        requirement.id
        for requirement in requirements
        if any(
            normalize_content(term) in normalized_answer
            for term in requirement.any_of
        )
    ]


def _snapshot_citation(citation: CitationDraft) -> GenerationCitationSnapshot:
    return GenerationCitationSnapshot(
        chunk_id=citation.chunk_id,
        poem_id=citation.poem_id,
        poem_version_id=citation.poem_version_id,
        annotation_id=citation.annotation_id,
        title=citation.title,
        author_name=citation.author_name,
        dynasty_name=citation.dynasty_name,
        granularity=citation.granularity,
        text=citation.text,
        score=citation.score,
        rank=citation.rank,
    )


def _summarize(
    results: list[GenerationEvaluationCaseResult],
) -> GenerationEvaluationSummary:
    answerable = [result for result in results if result.expected == "answer"]
    refusals = [result for result in results if result.expected == "refusal"]
    correct_refusals = sum(
        result.refused and result.error_code is None
        for result in refusals
    )
    wrong_refusals = sum(
        result.refused and result.error_code is None
        for result in answerable
    )
    missed_refusals = len(refusals) - correct_refusals
    refusal_precision = _rounded_ratio(
        correct_refusals,
        correct_refusals + wrong_refusals,
    )
    refusal_recall = _rounded_ratio(
        correct_refusals,
        correct_refusals + missed_refusals,
    )
    refusal_f1 = _rounded_f1(refusal_precision, refusal_recall)

    emitted_citations = sum(result.citation_count for result in results)
    matched_citations = sum(
        len(result.matched_citation_ranks)
        for result in results
    )
    expected_citations = sum(
        len(result.matched_expected_citation_indexes)
        + len(result.missing_expected_citation_indexes)
        for result in results
    )
    matched_expected_citations = sum(
        len(result.matched_expected_citation_indexes)
        for result in results
    )
    latencies = [result.latency_ms for result in results]

    return GenerationEvaluationSummary(
        total_cases=len(results),
        answerable_cases=len(answerable),
        refusal_cases=len(refusals),
        passed_cases=sum(result.passed for result in results),
        pass_rate=_rounded_ratio(
            sum(result.passed for result in results),
            len(results),
        ),
        answer_accuracy=_rounded_ratio(
            sum(result.answer_correct for result in answerable),
            len(answerable),
        )
        if answerable
        else None,
        refusal_accuracy=_rounded_ratio(
            sum(result.answer_correct for result in refusals),
            len(refusals),
        )
        if refusals
        else None,
        refusal_precision=refusal_precision,
        refusal_recall=refusal_recall,
        refusal_f1=refusal_f1,
        citation_precision=(
            _rounded_ratio(matched_citations, emitted_citations)
            if emitted_citations
            else None
        ),
        citation_recall=(
            _rounded_ratio(matched_expected_citations, expected_citations)
            if expected_citations
            else None
        ),
        average_latency_ms=round(statistics.fmean(latencies), 3) if latencies else 0.0,
        p95_latency_ms=_p95(latencies),
    )


def _summarize_strategy(
    results: list[GenerationEvaluationCaseResult],
) -> str | None:
    strategies = list(
        dict.fromkeys(
            result.strategy
            for result in results
            if result.strategy
        )
    )
    if not strategies:
        return None
    return ", ".join(strategies)


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    return 0


def _rounded_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _rounded_f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 6)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 3)


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6f}"
