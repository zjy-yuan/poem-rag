from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from app.core.text import normalize_content, normalize_lookup
from app.schemas.evaluation import (
    EvidenceSelector,
    RetrievalEvaluationCase,
    RetrievalEvaluationCaseResult,
    RetrievalEvaluationDataset,
    RetrievalEvaluationReport,
    RetrievalEvaluationSummary,
    RetrievalEvidenceSnapshot,
)
from app.schemas.retrieval import RetrievalEvidence
from app.services.retrieval import RetrievalSearchResult


class RetrievalSearchPort(Protocol):
    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
    ) -> RetrievalSearchResult: ...


def load_evaluation_dataset(path: Path) -> RetrievalEvaluationDataset:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RetrievalEvaluationDataset.model_validate(payload)


def matches_selector(
    evidence: RetrievalEvidence,
    selector: EvidenceSelector,
) -> bool:
    if selector.poem_title is not None and normalize_lookup(
        evidence.title
    ) != normalize_lookup(selector.poem_title):
        return False
    if selector.author_name is not None and (
        evidence.author_name is None
        or normalize_lookup(evidence.author_name)
        != normalize_lookup(selector.author_name)
    ):
        return False
    if selector.dynasty_name is not None and (
        evidence.dynasty_name is None
        or normalize_lookup(evidence.dynasty_name)
        != normalize_lookup(selector.dynasty_name)
    ):
        return False
    if selector.granularity is not None and evidence.granularity != selector.granularity:
        return False
    if selector.text_contains is not None and normalize_content(
        selector.text_contains
    ) not in normalize_content(evidence.text):
        return False
    if selector.line_start is not None and evidence.line_start != selector.line_start:
        return False
    if selector.line_end is not None and evidence.line_end != selector.line_end:
        return False
    return True


class RetrievalEvaluator:
    def __init__(self, retrieval: RetrievalSearchPort) -> None:
        self.retrieval = retrieval

    async def evaluate(
        self,
        dataset: RetrievalEvaluationDataset,
        *,
        top_k: int,
    ) -> RetrievalEvaluationReport:
        results: list[RetrievalEvaluationCaseResult] = []
        strategy = "unknown"

        for case in dataset.cases:
            started_at = perf_counter()
            search_result = await self.retrieval.search_evidence(
                query=case.question,
                limit=top_k,
            )
            latency_ms = (perf_counter() - started_at) * 1000
            strategy = search_result.strategy
            results.append(
                _evaluate_case(
                    case,
                    search_result.items,
                    latency_ms=latency_ms,
                )
            )

        categories = {
            category: _summarize(
                [result for result in results if result.category == category]
            )
            for category in dict.fromkeys(result.category for result in results)
        }
        return RetrievalEvaluationReport(
            dataset_version=dataset.version,
            strategy=strategy,
            top_k=top_k,
            generated_at=datetime.now(UTC),
            summary=_summarize(results),
            categories=categories,
            failure_case_ids=[result.case_id for result in results if not result.passed],
            results=results,
        )


def format_evaluation_report(report: RetrievalEvaluationReport) -> str:
    summary = report.summary
    lines = [
        f"dataset={report.dataset_version}",
        f"strategy={report.strategy} top_k={report.top_k}",
        (
            f"cases={summary.total_cases} passed={summary.passed_cases} "
            f"pass_rate={_format_optional(summary.pass_rate)}"
        ),
        (
            f"recall_at_k={_format_optional(summary.recall_at_k)} "
            f"mrr={_format_optional(summary.mrr)} "
            f"hit_rate_at_k={_format_optional(summary.hit_rate_at_k)}"
        ),
        (
            "answerable_no_result_rate="
            f"{_format_optional(summary.answerable_no_result_rate)} "
            "unanswerable_accuracy="
            f"{_format_optional(summary.unanswerable_accuracy)}"
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
            f"recall_at_k={_format_optional(category_summary.recall_at_k)} "
            f"mrr={_format_optional(category_summary.mrr)}"
        )
    if report.failure_case_ids:
        lines.extend(["", f"failures={', '.join(report.failure_case_ids)}"])
    return "\n".join(lines)


def _evaluate_case(
    case: RetrievalEvaluationCase,
    evidence: list[RetrievalEvidence],
    *,
    latency_ms: float,
) -> RetrievalEvaluationCaseResult:
    snapshots = [
        RetrievalEvidenceSnapshot.from_evidence(item)
        for item in evidence
    ]
    if case.expected == "no_evidence":
        passed = not evidence
        return RetrievalEvaluationCaseResult(
            case_id=case.id,
            category=case.category,
            question=case.question,
            expected=case.expected,
            passed=passed,
            retrieved_count=len(evidence),
            recall_at_k=None,
            reciprocal_rank=None,
            matched_gold=[],
            missing_gold=[],
            latency_ms=round(latency_ms, 3),
            retrieved_evidence=snapshots,
        )

    first_relevant_rank: int | None = None
    matched_gold: list[int] = []
    missing_gold: list[int] = []
    for gold_index, selector in enumerate(case.gold_evidence, start=1):
        rank = next(
            (
                evidence_index
                for evidence_index, item in enumerate(evidence, start=1)
                if matches_selector(item, selector)
            ),
            None,
        )
        if rank is None:
            missing_gold.append(gold_index)
            continue
        matched_gold.append(gold_index)
        if first_relevant_rank is None or rank < first_relevant_rank:
            first_relevant_rank = rank

    recall_at_k = len(matched_gold) / len(case.gold_evidence)
    reciprocal_rank = (
        1.0 / first_relevant_rank
        if first_relevant_rank is not None
        else 0.0
    )
    return RetrievalEvaluationCaseResult(
        case_id=case.id,
        category=case.category,
        question=case.question,
        expected=case.expected,
        passed=not missing_gold,
        retrieved_count=len(evidence),
        recall_at_k=round(recall_at_k, 6),
        reciprocal_rank=round(reciprocal_rank, 6),
        matched_gold=matched_gold,
        missing_gold=missing_gold,
        latency_ms=round(latency_ms, 3),
        retrieved_evidence=snapshots,
    )


def _summarize(
    results: list[RetrievalEvaluationCaseResult],
) -> RetrievalEvaluationSummary:
    answerable = [result for result in results if result.expected == "evidence"]
    unanswerable = [result for result in results if result.expected == "no_evidence"]
    latencies = [result.latency_ms for result in results]

    return RetrievalEvaluationSummary(
        total_cases=len(results),
        answerable_cases=len(answerable),
        unanswerable_cases=len(unanswerable),
        passed_cases=sum(result.passed for result in results),
        pass_rate=_rounded_ratio(
            sum(result.passed for result in results),
            len(results),
        ),
        recall_at_k=_rounded_mean(
            [
                result.recall_at_k
                for result in answerable
                if result.recall_at_k is not None
            ]
        ),
        mrr=_rounded_mean(
            [
                result.reciprocal_rank
                for result in answerable
                if result.reciprocal_rank is not None
            ]
        ),
        hit_rate_at_k=_rounded_ratio(
            sum(
                result.recall_at_k is not None and result.recall_at_k > 0
                for result in answerable
            ),
            len(answerable),
        ),
        answerable_no_result_rate=_rounded_ratio(
            sum(result.retrieved_count == 0 for result in answerable),
            len(answerable),
        ),
        unanswerable_accuracy=_rounded_ratio(
            sum(result.passed for result in unanswerable),
            len(unanswerable),
        ),
        average_latency_ms=round(statistics.fmean(latencies), 3) if latencies else 0.0,
        p95_latency_ms=_p95(latencies),
    )


def _rounded_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _rounded_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.fmean(values), 6)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 3)


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6f}"
