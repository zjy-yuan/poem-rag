from __future__ import annotations

from pathlib import Path

import pytest
from app.evaluation.retrieval import (
    RetrievalEvaluator,
    load_evaluation_dataset,
    matches_selector,
)
from app.models.chunk import ChunkGranularity
from app.schemas.evaluation import (
    EvidenceSelector,
    RetrievalEvaluationDataset,
)
from app.schemas.retrieval import RetrievalEvidence
from app.services.retrieval import RetrievalSearchResult

DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "retrieval_lexical_v1.json"
)


def _evidence(
    *,
    chunk_id: int,
    title: str,
    text: str,
    granularity: ChunkGranularity = ChunkGranularity.LINE,
    author_name: str | None = "李白",
    dynasty_name: str | None = "唐",
) -> RetrievalEvidence:
    return RetrievalEvidence(
        chunk_id=chunk_id,
        poem_id=1,
        poem_version_id=1,
        annotation_id=None,
        annotation_type=None,
        title=title,
        author_id=1,
        author_name=author_name,
        dynasty_id=1,
        dynasty_name=dynasty_name,
        granularity=granularity,
        chunk_index=chunk_id,
        text=text,
        line_start=chunk_id,
        line_end=chunk_id,
        chunk_strategy="structural-v1",
        status="pending",
        score=1.0,
        match_types=["chunk_exact"],
        published_at=None,
    )


class FakeRetrieval:
    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
    ) -> RetrievalSearchResult:
        del limit
        if "床前明月光" in query:
            items = [
                _evidence(
                    chunk_id=1,
                    title="静夜思",
                    text="床前明月光，疑是地上霜。",
                )
            ]
        elif "举头望明月" in query:
            items = [
                _evidence(chunk_id=2, title="春晓", text="春眠不觉晓"),
                _evidence(
                    chunk_id=3,
                    title="静夜思",
                    text="举头望明月，低头思故乡。",
                ),
            ]
        else:
            items = []
        return RetrievalSearchResult(
            items=items,
            strategy="test-strategy",
            normalized_query=query,
            candidate_count=len(items),
        )


def test_selector_matches_portable_evidence_fields() -> None:
    item = _evidence(
        chunk_id=1,
        title="静夜思",
        text="举头望明月，低头思故乡。",
    )
    selector = EvidenceSelector(
        poem_title="静夜思",
        author_name="李白",
        dynasty_name="唐",
        granularity=ChunkGranularity.LINE,
        text_contains="低头思故乡",
        line_start=1,
        line_end=1,
    )
    assert matches_selector(item, selector)

    assert not matches_selector(
        item,
        EvidenceSelector(poem_title="春晓"),
    )
    assert not matches_selector(
        item,
        EvidenceSelector(text_contains="黄河入海流"),
    )


@pytest.mark.asyncio
async def test_evaluator_reports_recall_mrr_and_no_evidence_accuracy() -> None:
    full_dataset = load_evaluation_dataset(DATASET_PATH)
    assert 20 <= len(full_dataset.cases) <= 30
    no_answer_case = next(
        case for case in full_dataset.cases if case.expected == "no_evidence"
    )
    dataset = RetrievalEvaluationDataset(
        version="test-subset-v1",
        description="Three cases used to verify evaluator arithmetic.",
        cases=[full_dataset.cases[0], full_dataset.cases[1], no_answer_case],
    )

    evaluator = RetrievalEvaluator(FakeRetrieval())
    report = await evaluator.evaluate(dataset, top_k=5)

    assert report.strategy == "test-strategy"
    assert report.summary.total_cases == 3
    assert report.summary.answerable_cases == 2
    assert report.summary.unanswerable_cases == 1
    assert report.summary.passed_cases == 3
    assert report.summary.recall_at_k == 1.0
    assert report.summary.mrr == 0.75
    assert report.summary.unanswerable_accuracy == 1.0
    assert report.results[0].matched_gold == [1]
    assert report.results[1].matched_gold == [1]
    assert report.results[1].reciprocal_rank == 0.5
