from __future__ import annotations

import importlib.util
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
    RetrievalEvaluationCase,
    RetrievalEvaluationDataset,
)
from app.schemas.retrieval import RetrievalEvidence
from app.services.retrieval import RetrievalSearchResult
from pydantic import ValidationError

DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "retrieval_lexical_v2.json"
)
OPEN_CORPUS_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "retrieval_open_corpus_v1.json"
)
HOLDOUT_1000_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "retrieval_holdout_1000_v1.json"
)
HOLDOUT_1000_V2_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "retrieval_holdout_1000_v2.json"
)
HOLDOUT_1000_V3_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "retrieval_holdout_1000_v3.json"
)
EVALUATE_RETRIEVAL_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "evaluate_retrieval.py"
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
    def __init__(self, *, suppressed_phrases: tuple[str, ...] = ()) -> None:
        self.suppressed_phrases = suppressed_phrases

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
    ) -> RetrievalSearchResult:
        del limit
        if any(phrase in query for phrase in self.suppressed_phrases):
            items = []
        elif "床前明月光" in query:
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
    assert 31 <= len(full_dataset.cases) <= 40
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
    assert report.summary.refusal_precision == 1.0
    assert report.summary.refusal_recall == 1.0
    assert report.summary.refusal_f1 == 1.0
    assert report.results[0].matched_gold == [1]
    assert report.results[1].matched_gold == [1]
    assert report.results[1].reciprocal_rank == 0.5


@pytest.mark.asyncio
async def test_evaluator_penalizes_refusing_an_answerable_question() -> None:
    full_dataset = load_evaluation_dataset(DATASET_PATH)
    no_answer_case = next(
        case for case in full_dataset.cases if case.expected == "no_evidence"
    )
    dataset = RetrievalEvaluationDataset(
        version="test-subset-v1",
        description="Two answerable cases and one unanswerable case.",
        cases=[full_dataset.cases[0], full_dataset.cases[1], no_answer_case],
    )

    evaluator = RetrievalEvaluator(
        FakeRetrieval(suppressed_phrases=("举头望明月",))
    )
    report = await evaluator.evaluate(dataset, top_k=5)

    assert report.summary.passed_cases == 2
    assert report.summary.answerable_no_result_rate == 0.5
    assert report.summary.unanswerable_accuracy == 1.0
    assert report.summary.refusal_precision == 0.5
    assert report.summary.refusal_recall == 1.0
    assert report.summary.refusal_f1 == 0.666667


def test_v2_dataset_separates_answerability_categories() -> None:
    dataset = load_evaluation_dataset(DATASET_PATH)
    assert dataset.version == "lexical-baseline-seed-v2"
    categories = {case.category for case in dataset.cases}
    assert {
        "no_answer_cross_domain",
        "no_answer_in_domain_missing_entity",
        "no_answer_in_domain_missing_attribute",
    } <= categories


def test_open_corpus_dataset_has_expected_coverage() -> None:
    dataset = load_evaluation_dataset(OPEN_CORPUS_DATASET_PATH)
    answerable = [case for case in dataset.cases if case.expected == "evidence"]
    unanswerable = [
        case for case in dataset.cases if case.expected == "no_evidence"
    ]

    assert dataset.version == "open-corpus-100-v1"
    assert len(dataset.cases) == 50
    assert len(answerable) == 42
    assert len(unanswerable) == 8
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)

    categories = {case.category for case in dataset.cases}
    assert {
        "exact_quote",
        "phrase",
        "title",
        "author",
        "dynasty",
        "multi_evidence",
        "natural_language",
        "long_form",
        "no_answer_cross_domain",
        "no_answer_in_domain_missing_entity",
        "no_answer_in_domain_missing_attribute",
    } <= categories


def test_holdout_1000_dataset_has_expected_coverage() -> None:
    dataset = load_evaluation_dataset(HOLDOUT_1000_DATASET_PATH)
    answerable = [case for case in dataset.cases if case.expected == "evidence"]
    unanswerable = [
        case for case in dataset.cases if case.expected == "no_evidence"
    ]

    assert dataset.version == "retrieval-holdout-1000-v1"
    assert len(dataset.cases) == 50
    assert len(answerable) == 42
    assert len(unanswerable) == 8
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)

    categories = {case.category for case in dataset.cases}
    assert {
        "exact_quote",
        "phrase",
        "title",
        "author",
        "dynasty",
        "multi_evidence",
        "natural_language",
        "long_form",
        "no_answer_cross_domain",
        "no_answer_in_domain_missing_entity",
        "no_answer_in_domain_missing_attribute",
    } <= categories


def test_holdout_1000_v2_dataset_has_expected_coverage() -> None:
    dataset = load_evaluation_dataset(HOLDOUT_1000_V2_DATASET_PATH)
    previous_dataset = load_evaluation_dataset(HOLDOUT_1000_DATASET_PATH)
    answerable = [case for case in dataset.cases if case.expected == "evidence"]
    unanswerable = [
        case for case in dataset.cases if case.expected == "no_evidence"
    ]

    assert dataset.version == "retrieval-holdout-1000-v2"
    assert len(dataset.cases) == 46
    assert len(answerable) == 38
    assert len(unanswerable) == 8
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)
    assert {case.question for case in dataset.cases}.isdisjoint(
        {case.question for case in previous_dataset.cases}
    )

    categories = {case.category for case in dataset.cases}
    assert {
        "exact_quote",
        "phrase",
        "title",
        "author",
        "dynasty",
        "multi_evidence",
        "natural_language",
        "long_form",
        "structured_filter",
        "no_answer_cross_domain",
        "no_answer_in_domain_missing_entity",
        "no_answer_in_domain_missing_attribute",
    } <= categories


def test_holdout_1000_v2_gold_evidence_exists_in_converted_corpus(
    converted_corpus_records: list[dict[str, object]],
) -> None:
    dataset = load_evaluation_dataset(HOLDOUT_1000_V2_DATASET_PATH)
    records = converted_corpus_records

    for case in dataset.cases:
        for selector in case.gold_evidence:
            matches = [
                record
                for record in records
                if selector.poem_title is None
                or record["title"] == selector.poem_title
            ]
            if selector.author_name is not None:
                matches = [
                    record
                    for record in matches
                    if record["author_name"] == selector.author_name
                ]
            if selector.dynasty_name is not None:
                matches = [
                    record
                    for record in matches
                    if record["dynasty_name"] == selector.dynasty_name
                ]
            if selector.text_contains is not None:
                matches = [
                    record
                    for record in matches
                    if selector.text_contains in record["content"]
                ]
            assert matches, f"{case.id} 的金标准无法在当前转换后语料中定位"


def test_holdout_1000_v3_dataset_has_expected_coverage() -> None:
    dataset = load_evaluation_dataset(HOLDOUT_1000_V3_DATASET_PATH)
    previous_datasets = [
        load_evaluation_dataset(HOLDOUT_1000_DATASET_PATH),
        load_evaluation_dataset(HOLDOUT_1000_V2_DATASET_PATH),
    ]
    answerable = [case for case in dataset.cases if case.expected == "evidence"]
    unanswerable = [
        case for case in dataset.cases if case.expected == "no_evidence"
    ]

    assert dataset.version == "retrieval-holdout-1000-v3"
    assert len(dataset.cases) == 46
    assert len(answerable) == 38
    assert len(unanswerable) == 8
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)
    assert {case.question for case in dataset.cases}.isdisjoint(
        {
            case.question
            for previous_dataset in previous_datasets
            for case in previous_dataset.cases
        }
    )

    categories = {case.category for case in dataset.cases}
    assert {
        "exact_quote",
        "phrase",
        "title",
        "author",
        "dynasty",
        "multi_evidence",
        "natural_language",
        "long_form",
        "structured_filter",
        "no_answer_cross_domain",
        "no_answer_in_domain_missing_entity",
        "no_answer_in_domain_missing_attribute",
    } <= categories


def test_holdout_1000_v3_gold_evidence_exists_in_converted_corpus(
    converted_corpus_records: list[dict[str, object]],
) -> None:
    dataset = load_evaluation_dataset(HOLDOUT_1000_V3_DATASET_PATH)
    records = converted_corpus_records

    for case in dataset.cases:
        for selector in case.gold_evidence:
            matches = [
                record
                for record in records
                if selector.poem_title is None
                or record["title"] == selector.poem_title
            ]
            if selector.author_name is not None:
                matches = [
                    record
                    for record in matches
                    if record["author_name"] == selector.author_name
                ]
            if selector.dynasty_name is not None:
                matches = [
                    record
                    for record in matches
                    if record["dynasty_name"] == selector.dynasty_name
                ]
            if selector.text_contains is not None:
                matches = [
                    record
                    for record in matches
                    if selector.text_contains in record["content"]
                ]
            assert matches, f"{case.id} 的金标准无法在当前转换后语料中定位"


def test_retrieval_dataset_rejects_duplicate_questions() -> None:
    case = RetrievalEvaluationCase(
        id="holdout-duplicate-01",
        category="phrase",
        question="明月",
        expected="evidence",
        gold_evidence=[EvidenceSelector(poem_title="静夜思")],
    )

    with pytest.raises(ValidationError, match="question 必须唯一"):
        RetrievalEvaluationDataset(
            version="duplicate-test-v1",
            description="Duplicate questions must fail validation.",
            cases=[
                case,
                case.model_copy(update={"id": "holdout-duplicate-02"}),
            ],
        )


def test_retrieval_evaluation_uses_configured_dense_min_score() -> None:
    spec = importlib.util.spec_from_file_location(
        "evaluate_retrieval_script",
        EVALUATE_RETRIEVAL_SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.resolve_min_score(
        strategy="dense",
        min_score=None,
        configured_min_score=0.6,
    ) == 0.6
    assert module.resolve_min_score(
        strategy="expanded-hybrid",
        min_score=0.42,
        configured_min_score=0.6,
    ) == 0.42
    assert module.resolve_min_score(
        strategy="expanded",
        min_score=None,
        configured_min_score=0.6,
    ) == 0.0
