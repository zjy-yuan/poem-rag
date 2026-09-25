from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from app.ai.graphs.rag import NO_EVIDENCE_ANSWER, CitationDraft
from app.ai.providers.chat import ChatMessage, ChatModelError
from app.core.errors import ErrorCode
from app.evaluation.generation import (
    GenerationEvaluator,
    load_generation_evaluation_dataset,
)
from app.schemas.evaluation import EvidenceSelector
from app.schemas.generation_evaluation import (
    FactRequirement,
    GenerationEvaluationCase,
    GenerationEvaluationDataset,
)
from pydantic import ValidationError

DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "generation_rag_v1.json"
)
OPEN_CORPUS_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "generation_rag_open_corpus_v1.json"
)
HOLDOUT_1000_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "generation_holdout_1000_v1.json"
)
HOLDOUT_1000_V2_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "generation_holdout_1000_v2.json"
)
HOLDOUT_1000_V3_DATASET_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "eval"
    / "generation_holdout_1000_v3.json"
)
EVALUATE_GENERATION_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "evaluate_generation.py"
)


def _citation(
    *,
    title: str = "静夜思",
    text: str = "举头望明月，低头思故乡。",
    rank: int = 1,
) -> CitationDraft:
    return CitationDraft(
        chunk_id=rank,
        poem_id=1,
        poem_version_id=1,
        annotation_id=None,
        title=title,
        author_name="李白",
        dynasty_name="唐",
        granularity="line",
        text=text,
        score=1.0,
        rank=rank,
    )


class FakeGraph:
    def __init__(
        self,
        *,
        events_by_query: dict[str, list[dict[str, Any]]],
        errors_by_query: dict[str, Exception] | None = None,
    ) -> None:
        self.events_by_query = events_by_query
        self.errors_by_query = errors_by_query or {}

    async def stream(
        self,
        *,
        query: str,
        history: Sequence[ChatMessage],
    ) -> AsyncIterator[dict[str, Any]]:
        del history
        error = self.errors_by_query.get(query)
        if error is not None:
            raise error
        for event in self.events_by_query.get(query, []):
            yield event


@asynccontextmanager
async def _open_graph(graph: FakeGraph) -> AsyncIterator[FakeGraph]:
    yield graph


class FakeGraphFactory:
    def __init__(
        self,
        *,
        events_by_query: dict[str, list[dict[str, Any]]],
        errors_by_query: dict[str, Exception] | None = None,
    ) -> None:
        self.graph = FakeGraph(
            events_by_query=events_by_query,
            errors_by_query=errors_by_query,
        )

    def __call__(self) -> AbstractAsyncContextManager[FakeGraph]:
        return _open_graph(self.graph)


def _answer_case(
    *,
    case_id: str,
    question: str,
    required_fact_id: str = "moon",
    required_term: str = "明月",
) -> GenerationEvaluationCase:
    return GenerationEvaluationCase(
        id=case_id,
        category="answer",
        question=question,
        expected="answer",
        required_facts=[
            FactRequirement(id=required_fact_id, any_of=[required_term])
        ],
        expected_citations=[EvidenceSelector(poem_title="静夜思")],
    )


@pytest.mark.asyncio
async def test_generation_evaluator_reports_answer_citation_and_refusal_metrics() -> None:
    dataset = GenerationEvaluationDataset(
        version="generation-test-v1",
        description="Deterministic metrics for the generation evaluator.",
        cases=[
            _answer_case(
                case_id="answer-good",
                question="good answer",
            ),
            _answer_case(
                case_id="answer-refused",
                question="refused answer",
            ),
            GenerationEvaluationCase(
                id="refusal-good",
                category="refusal",
                question="good refusal",
                expected="refusal",
            ),
            GenerationEvaluationCase(
                id="refusal-missed",
                category="refusal",
                question="missed refusal",
                expected="refusal",
            ),
        ],
    )
    factory = FakeGraphFactory(
        events_by_query={
            "good answer": [
                {
                    "kind": "retrieval",
                    "candidate_count": 3,
                    "selected_count": 1,
                    "strategy": "test-strategy",
                },
                {
                    "kind": "assessment",
                    "status": "passed",
                    "reason_code": "supported",
                },
                {"kind": "delta", "text": "明月常与思乡相连。[1]"},
                {"kind": "citation", "citation": _citation()},
                {
                    "kind": "final",
                    "answer": "明月常与思乡相连。[1]",
                    "citations": [_citation()],
                    "finish_reason": "stop",
                },
            ],
            "refused answer": [
                {
                    "kind": "assessment",
                    "status": "refused",
                    "reason_code": "missing_fact",
                },
                {"kind": "delta", "text": NO_EVIDENCE_ANSWER},
                {
                    "kind": "final",
                    "answer": NO_EVIDENCE_ANSWER,
                    "citations": [],
                    "finish_reason": "no_evidence",
                },
            ],
            "good refusal": [
                {"kind": "delta", "text": NO_EVIDENCE_ANSWER},
                {
                    "kind": "final",
                    "answer": NO_EVIDENCE_ANSWER,
                    "citations": [],
                    "finish_reason": "no_evidence",
                },
            ],
            "missed refusal": [
                {"kind": "delta", "text": "李白出生于碎叶城。[1]"},
                {"kind": "citation", "citation": _citation()},
                {
                    "kind": "final",
                    "answer": "李白出生于碎叶城。[1]",
                    "citations": [_citation()],
                    "finish_reason": "stop",
                },
            ],
        }
    )

    report = await GenerationEvaluator(
        factory,
        model="fake-generation-model",
    ).evaluate(dataset)

    assert report.model == "fake-generation-model"
    assert report.strategy == "test-strategy"
    assert report.summary.total_cases == 4
    assert report.summary.answerable_cases == 2
    assert report.summary.refusal_cases == 2
    assert report.summary.passed_cases == 2
    assert report.summary.pass_rate == 0.5
    assert report.summary.answer_accuracy == 0.5
    assert report.summary.refusal_accuracy == 0.5
    assert report.summary.refusal_precision == 0.5
    assert report.summary.refusal_recall == 0.5
    assert report.summary.refusal_f1 == 0.5
    assert report.summary.citation_precision == 0.5
    assert report.summary.citation_recall == 0.5
    assert report.failure_case_ids == ["answer-refused", "refusal-missed"]
    assert report.results[0].required_fact_matches == {"moon": "明月"}
    assert report.results[0].matched_citation_ranks == [1]
    assert report.results[0].passed is True
    assert report.results[0].assessment_status == "passed"
    assert report.results[0].assessment_reason_code == "supported"
    assert report.results[1].refused is True
    assert report.results[1].answer_correct is False
    assert report.results[1].assessment_status == "refused"
    assert report.results[1].assessment_reason_code == "missing_fact"
    assert report.results[1].missing_expected_citation_indexes == [1]
    assert report.results[3].citation_precision == 0.0
    assert report.results[3].assessment_status is None


@pytest.mark.asyncio
async def test_generation_evaluator_isolates_graph_errors_per_case() -> None:
    dataset = GenerationEvaluationDataset(
        version="generation-error-test-v1",
        description="One failing case must not abort the whole evaluation.",
        cases=[
            _answer_case(
                case_id="answer-error",
                question="provider error",
            ),
            GenerationEvaluationCase(
                id="refusal-after-error",
                category="refusal",
                question="refusal after error",
                expected="refusal",
            ),
        ],
    )
    factory = FakeGraphFactory(
        events_by_query={
            "refusal after error": [
                {
                    "kind": "final",
                    "answer": NO_EVIDENCE_ANSWER,
                    "citations": [],
                    "finish_reason": "no_evidence",
                }
            ]
        },
        errors_by_query={
            "provider error": ChatModelError(
                "模型响应超时",
                code=ErrorCode.MODEL_TIMEOUT,
            )
        },
    )

    report = await GenerationEvaluator(
        factory,
        model="fake-generation-model",
    ).evaluate(dataset)

    assert report.failure_case_ids == ["answer-error"]
    assert report.results[0].error_code == ErrorCode.MODEL_TIMEOUT.value
    assert report.results[1].passed is True
    assert report.summary.pass_rate == 0.5


def test_generation_dataset_has_answer_and_refusal_coverage() -> None:
    dataset = load_generation_evaluation_dataset(DATASET_PATH)

    assert dataset.version == "generation-rag-seed-v1"
    assert len(dataset.cases) == 12
    assert sum(case.expected == "answer" for case in dataset.cases) == 8
    assert sum(case.expected == "refusal" for case in dataset.cases) == 4
    assert {
        "moon_homesickness",
        "emotion",
        "scene",
        "philosophy",
        "imagery",
        "theme",
        "refusal_missing_attribute",
        "refusal_missing_entity",
        "refusal_cross_domain",
    } <= {case.category for case in dataset.cases}


def test_open_corpus_generation_dataset_has_holdout_coverage() -> None:
    dataset = load_generation_evaluation_dataset(OPEN_CORPUS_DATASET_PATH)

    assert dataset.version == "generation-rag-open-corpus-100-v1"
    assert len(dataset.cases) == 28
    assert sum(case.expected == "answer" for case in dataset.cases) == 21
    assert sum(case.expected == "refusal" for case in dataset.cases) == 7
    assert {
        "poem_fact",
        "natural_language",
        "long_form",
        "allusion",
        "multi_evidence",
        "refusal_missing_entity",
        "refusal_missing_attribute",
        "refusal_cross_domain",
    } <= {case.category for case in dataset.cases}
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)


def test_holdout_1000_generation_dataset_has_expected_coverage() -> None:
    dataset = load_generation_evaluation_dataset(HOLDOUT_1000_DATASET_PATH)

    assert dataset.version == "generation-holdout-1000-v1"
    assert len(dataset.cases) == 28
    assert sum(case.expected == "answer" for case in dataset.cases) == 21
    assert sum(case.expected == "refusal" for case in dataset.cases) == 7
    assert {
        "poem_fact",
        "natural_language",
        "long_form",
        "allusion",
        "multi_evidence",
        "refusal_missing_entity",
        "refusal_missing_attribute",
        "refusal_cross_domain",
    } <= {case.category for case in dataset.cases}
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)


def test_holdout_1000_v2_generation_dataset_has_expected_coverage() -> None:
    dataset = load_generation_evaluation_dataset(HOLDOUT_1000_V2_DATASET_PATH)
    previous_dataset = load_generation_evaluation_dataset(
        HOLDOUT_1000_DATASET_PATH
    )

    assert dataset.version == "generation-holdout-1000-v2"
    assert len(dataset.cases) == 26
    assert sum(case.expected == "answer" for case in dataset.cases) == 16
    assert sum(case.expected == "refusal" for case in dataset.cases) == 10
    assert {
        "poem_fact",
        "natural_language",
        "multi_evidence",
        "refusal_missing_entity",
        "refusal_missing_attribute",
        "refusal_cross_domain",
    } <= {case.category for case in dataset.cases}
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)
    assert {case.question for case in dataset.cases}.isdisjoint(
        {case.question for case in previous_dataset.cases}
    )


def test_holdout_1000_v2_citations_exist_in_converted_corpus(
    converted_corpus_records: list[dict[str, object]],
) -> None:
    dataset = load_generation_evaluation_dataset(HOLDOUT_1000_V2_DATASET_PATH)
    records = converted_corpus_records

    for case in dataset.cases:
        for selector in case.expected_citations:
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
            assert matches, f"{case.id} 的引用金标准无法在当前转换后语料中定位"


def test_holdout_1000_v3_generation_dataset_has_expected_coverage() -> None:
    dataset = load_generation_evaluation_dataset(HOLDOUT_1000_V3_DATASET_PATH)
    previous_datasets = [
        load_generation_evaluation_dataset(HOLDOUT_1000_DATASET_PATH),
        load_generation_evaluation_dataset(HOLDOUT_1000_V2_DATASET_PATH),
    ]

    assert dataset.version == "generation-holdout-1000-v3"
    assert len(dataset.cases) == 26
    assert sum(case.expected == "answer" for case in dataset.cases) == 16
    assert sum(case.expected == "refusal" for case in dataset.cases) == 10
    assert {
        "poem_fact",
        "natural_language",
        "multi_evidence",
        "refusal_missing_entity",
        "refusal_missing_attribute",
        "refusal_cross_domain",
    } <= {case.category for case in dataset.cases}
    assert len({case.id for case in dataset.cases}) == len(dataset.cases)
    assert len({case.question for case in dataset.cases}) == len(dataset.cases)
    assert {case.question for case in dataset.cases}.isdisjoint(
        {
            case.question
            for previous_dataset in previous_datasets
            for case in previous_dataset.cases
        }
    )


def test_holdout_1000_v3_citations_exist_in_converted_corpus(
    converted_corpus_records: list[dict[str, object]],
) -> None:
    dataset = load_generation_evaluation_dataset(HOLDOUT_1000_V3_DATASET_PATH)
    records = converted_corpus_records

    for case in dataset.cases:
        for selector in case.expected_citations:
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
            assert matches, f"{case.id} 的引用金标准无法在当前转换后语料中定位"


def test_generation_evaluation_cli_defaults_to_1000_holdout() -> None:
    spec = importlib.util.spec_from_file_location(
        "evaluate_generation_script",
        EVALUATE_GENERATION_SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.DEFAULT_DATASET == HOLDOUT_1000_DATASET_PATH


def test_generation_dataset_rejects_duplicate_questions() -> None:
    case = GenerationEvaluationCase(
        id="holdout-duplicate-01",
        category="refusal",
        question="《静夜思》写于哪一年？",
        expected="refusal",
    )

    with pytest.raises(ValidationError, match="question 必须唯一"):
        GenerationEvaluationDataset(
            version="duplicate-test-v1",
            description="Duplicate questions must fail validation.",
            cases=[
                case,
                case.model_copy(update={"id": "holdout-duplicate-02"}),
            ],
        )


def test_generation_case_rejects_refusal_with_gold_facts() -> None:
    with pytest.raises(ValidationError):
        GenerationEvaluationCase(
            id="invalid-refusal",
            category="refusal",
            question="invalid",
            expected="refusal",
            required_facts=[
                FactRequirement(id="fact", any_of=["证据"])
            ],
        )
