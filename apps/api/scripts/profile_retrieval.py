from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_DATASET = (
    PROJECT_ROOT / "data" / "eval" / "retrieval_holdout_1000_v2.json"
)
DEFAULT_BASELINE_REPORT = (
    PROJECT_ROOT
    / "data"
    / "eval"
    / "reports"
    / "retrieval_holdout_1000_v2_expanded_hybrid_baseline.json"
)


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    rank: int
    chunk_id: int
    poem_id: int
    poem_version_id: int
    title: str
    granularity: str
    chunk_index: int
    score: float
    match_types: tuple[str, ...]
    text_preview: str


@dataclass(frozen=True, slots=True)
class BranchCall:
    query: str
    elapsed_ms: float
    item_count: int
    candidate_count: int
    hard_filtered: bool
    gold_match_ranks: tuple[int | None, ...]
    top_candidates: tuple[CandidateSnapshot, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingCall:
    text: str
    elapsed_ms: float
    dimension: int
    input_count: int = 1


@dataclass(frozen=True, slots=True)
class VectorCall:
    limit: int
    hit_count: int
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class RewriteProfile:
    elapsed_ms: float
    variants: tuple[str, ...]
    matched_concepts: tuple[str, ...]
    matched_entities: tuple[str, ...]
    matched_authors: tuple[str, ...]
    matched_dynasties: tuple[str, ...]
    explicit_titles: tuple[str, ...]
    content_terms: tuple[str, ...]
    multi_evidence: bool
    prefer_tail: bool


@dataclass(frozen=True, slots=True)
class CaseProfile:
    case_id: str
    question: str
    expected: str
    total_ms: float
    rewrite: RewriteProfile
    expanded: BranchCall
    variants: tuple[BranchCall, ...]
    lexical_calls: tuple[BranchCall, ...]
    dense_calls: tuple[BranchCall, ...]
    embedding_calls: tuple[EmbeddingCall, ...]
    vector_calls: tuple[VectorCall, ...]
    context_ms: float
    context_version_count: int
    context_result_count: int
    result_count: int
    result_titles: tuple[str, ...]
    final_items: tuple[CandidateSnapshot, ...]


@dataclass(slots=True)
class TimedRetrievalBranch:
    branch: Any
    gold_evidence: tuple[Any, ...] = ()
    top_n: int = 5
    calls: list[BranchCall] = field(default_factory=list)

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[Any] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> Any:
        started_at = perf_counter()
        result = await self.branch.search_evidence(
            query=query,
            limit=limit,
            granularities=granularities,
            author_id=author_id,
            dynasty_id=dynasty_id,
        )
        self.calls.append(
            BranchCall(
                query=query,
                elapsed_ms=_elapsed_ms(started_at),
                item_count=len(result.items),
                candidate_count=result.candidate_count,
                hard_filtered=result.hard_filtered,
                gold_match_ranks=_gold_match_ranks(
                    result.items,
                    gold_evidence=self.gold_evidence,
                ),
                top_candidates=_candidate_snapshots(
                    result.items,
                    limit=self.top_n,
                ),
            )
        )
        return result

    async def search_evidence_batch(
        self,
        requests: Sequence[Any],
    ) -> list[Any]:
        started_at = perf_counter()
        results = await self.branch.search_evidence_batch(requests)
        elapsed_ms = _elapsed_ms(started_at)
        for request, result in zip(requests, results, strict=True):
            self.calls.append(
                BranchCall(
                    query=request.query,
                    elapsed_ms=elapsed_ms,
                    item_count=len(result.items),
                    candidate_count=result.candidate_count,
                    hard_filtered=result.hard_filtered,
                    gold_match_ranks=_gold_match_ranks(
                        result.items,
                        gold_evidence=self.gold_evidence,
                    ),
                    top_candidates=_candidate_snapshots(
                        result.items,
                        limit=self.top_n,
                    ),
                )
            )
        return results


@dataclass(slots=True)
class TimedQueryRewriter:
    rewriter: Any
    elapsed_ms: float = 0.0
    last_rewrite: Any = None

    async def rewrite(self, query: str) -> Any:
        started_at = perf_counter()
        rewrite = await self.rewriter.rewrite(query)
        self.elapsed_ms = _elapsed_ms(started_at)
        self.last_rewrite = rewrite
        return rewrite


@dataclass(slots=True)
class TimedEmbeddingProvider:
    provider: Any
    calls: list[EmbeddingCall] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def dimension(self) -> int | None:
        return self.provider.dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        started_at = perf_counter()
        vectors = await self.provider.embed_documents(texts)
        self.calls.append(
            EmbeddingCall(
                text=f"[batch:{len(texts)}] " + " | ".join(texts),
                elapsed_ms=_elapsed_ms(started_at),
                dimension=len(vectors[0]) if vectors else 0,
                input_count=len(texts),
            )
        )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        started_at = perf_counter()
        vector = await self.provider.embed_query(text)
        self.calls.append(
            EmbeddingCall(
                text=text,
                elapsed_ms=_elapsed_ms(started_at),
                dimension=len(vector),
                input_count=1,
            )
        )
        return vector


@dataclass(slots=True)
class TimedVectorStore:
    store: Any
    calls: list[VectorCall] = field(default_factory=list)

    @property
    def collection(self) -> str:
        return self.store.collection

    async def ensure_collection(self, *, dimension: int) -> None:
        await self.store.ensure_collection(dimension=dimension)

    async def upsert(self, points: list[Any]) -> None:
        await self.store.upsert(points)

    async def delete(self, point_ids: list[str]) -> None:
        await self.store.delete(point_ids)

    async def search(self, request: Any) -> list[Any]:
        started_at = perf_counter()
        hits = await self.store.search(request)
        self.calls.append(
            VectorCall(
                limit=request.limit,
                hit_count=len(hits),
                elapsed_ms=_elapsed_ms(started_at),
            )
        )
        return hits


@dataclass(slots=True)
class TimedContextSource:
    source: Any
    elapsed_ms: float = 0.0
    version_count: int = 0
    result_count: int = 0

    async def list_poem_chunks_by_version_ids(
        self,
        *,
        version_ids: list[int],
    ) -> list[Any]:
        started_at = perf_counter()
        candidates = await self.source.list_poem_chunks_by_version_ids(
            version_ids=version_ids,
        )
        self.elapsed_ms = _elapsed_ms(started_at)
        self.version_count = len(version_ids)
        self.result_count = len(candidates)
        return candidates


async def run_profile(
    dataset_path: Path,
    baseline_report_path: Path,
    case_ids: list[str],
    *,
    top_k: int,
) -> list[CaseProfile]:
    from app.ai.providers.qdrant import create_qdrant_vector_store
    from app.ai.providers.qwen_embedding import create_qwen_embedding_provider
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.evaluation.retrieval import load_evaluation_dataset
    from app.repositories.chunks import ChunkRepository
    from app.services.dense_retrieval import DenseRetrievalService
    from app.services.evidence_context import PoemContextRetrievalService
    from app.services.hybrid_retrieval import HybridRetrievalService
    from app.services.query_expansion import (
        ExpandedRetrievalService,
        LexiconQueryRewriter,
        RepositoryQueryEntityResolver,
    )
    from app.services.retrieval import RetrievalService

    dataset = load_evaluation_dataset(dataset_path)
    cases_by_id = {case.id: case for case in dataset.cases}
    selected_case_ids = case_ids or _failure_case_ids(baseline_report_path)
    missing_case_ids = sorted(set(selected_case_ids) - set(cases_by_id))
    if missing_case_ids:
        raise ValueError(
            "dataset does not contain case ids: "
            + ", ".join(missing_case_ids)
        )

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    embedding_provider = create_qwen_embedding_provider(settings)
    vector_store = create_qdrant_vector_store(settings)
    profiles: list[CaseProfile] = []
    try:
        async with session_factory() as session:
            for case_id in selected_case_ids:
                case = cases_by_id[case_id]
                rewriter = TimedQueryRewriter(LexiconQueryRewriter())
                lexical = TimedRetrievalBranch(
                    RetrievalService(session),
                    gold_evidence=tuple(case.gold_evidence),
                )
                embedding = TimedEmbeddingProvider(embedding_provider)
                vector = TimedVectorStore(vector_store)
                dense = TimedRetrievalBranch(
                    DenseRetrievalService(
                        session,
                        embedding_provider=embedding,
                        vector_store=vector,
                        min_score=settings.chat_dense_min_score,
                    ),
                    gold_evidence=tuple(case.gold_evidence),
                )
                hybrid = HybridRetrievalService(
                    lexical,
                    dense,
                    fallback_on_dense_error=True,
                )
                hybrid_timer = TimedRetrievalBranch(
                    hybrid,
                    gold_evidence=tuple(case.gold_evidence),
                )
                expanded = ExpandedRetrievalService(
                    hybrid_timer,
                    rewriter,
                    strategy_name="expanded-hybrid-rrf-v1",
                    entity_resolver=RepositoryQueryEntityResolver(session),
                    max_variants=settings.chat_query_variant_limit,
                )
                expanded_timer = TimedRetrievalBranch(
                    expanded,
                    gold_evidence=tuple(case.gold_evidence),
                )
                context_source = TimedContextSource(ChunkRepository(session))
                retrieval = PoemContextRetrievalService(
                    expanded_timer,
                    context_source,
                )

                started_at = perf_counter()
                result = await retrieval.search_evidence(
                    query=case.question,
                    limit=top_k,
                )
                total_ms = _elapsed_ms(started_at)
                rewrite = rewriter.last_rewrite
                if rewrite is None:
                    raise RuntimeError("query rewriter did not run")

                profiles.append(
                    CaseProfile(
                        case_id=case.id,
                        question=case.question,
                        expected=case.expected,
                        total_ms=total_ms,
                        rewrite=RewriteProfile(
                            elapsed_ms=rewriter.elapsed_ms,
                            variants=tuple(rewrite.variants),
                            matched_concepts=tuple(rewrite.matched_concepts),
                            matched_entities=tuple(rewrite.matched_entities),
                            matched_authors=tuple(rewrite.matched_authors),
                            matched_dynasties=tuple(rewrite.matched_dynasties),
                            explicit_titles=tuple(rewrite.explicit_titles),
                            content_terms=tuple(rewrite.content_terms),
                            multi_evidence=rewrite.multi_evidence,
                            prefer_tail=rewrite.prefer_tail,
                        ),
                        expanded=expanded_timer.calls[0],
                        variants=tuple(hybrid_timer.calls),
                        lexical_calls=tuple(lexical.calls),
                        dense_calls=tuple(dense.calls),
                        embedding_calls=tuple(embedding.calls),
                        vector_calls=tuple(vector.calls),
                        context_ms=context_source.elapsed_ms,
                        context_version_count=context_source.version_count,
                        context_result_count=context_source.result_count,
                        result_count=len(result.items),
                        result_titles=tuple(
                            dict.fromkeys(item.title for item in result.items)
                        ),
                        final_items=_candidate_snapshots(
                            result.items,
                            limit=top_k,
                        ),
                    )
                )
    finally:
        await embedding_provider.aclose()
        await vector_store.aclose()
        await engine.dispose()

    return profiles


def _failure_case_ids(report_path: Path) -> list[str]:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    case_ids = payload.get("failure_case_ids")
    if not isinstance(case_ids, list) or not all(
        isinstance(case_id, str) for case_id in case_ids
    ):
        raise ValueError(f"invalid failure_case_ids in {report_path}")
    return case_ids


def format_profiles(profiles: list[CaseProfile]) -> str:
    lines: list[str] = []
    for profile in profiles:
        lines.extend(
            [
                f"\n[{profile.case_id}] {profile.question}",
                (
                    f"  expected={profile.expected} total={profile.total_ms:.3f}ms "
                    f"results={profile.result_count}"
                ),
                (
                    "  rewrite="
                    f"{profile.rewrite.elapsed_ms:.3f}ms "
                    f"variants={list(profile.rewrite.variants)}"
                ),
                (
                    "  concepts="
                    f"{list(profile.rewrite.matched_concepts)} "
                    f"entities={list(profile.rewrite.matched_entities)} "
                    f"authors={list(profile.rewrite.matched_authors)} "
                    f"dynasties={list(profile.rewrite.matched_dynasties)} "
                    f"multi={profile.rewrite.multi_evidence} "
                    f"tail={profile.rewrite.prefer_tail}"
                ),
                (
                    f"  expanded={profile.expanded.elapsed_ms:.3f}ms "
                    f"candidates={profile.expanded.candidate_count}"
                ),
                "  variants:",
            ]
        )
        for call in profile.variants:
            lines.append(
                f"    {call.elapsed_ms:9.3f}ms items={call.item_count:3d} "
                f"candidates={call.candidate_count:3d} "
                f"gold={list(call.gold_match_ranks)} query={call.query}"
            )
            for candidate in call.top_candidates:
                lines.append(
                    f"      #{candidate.rank} {candidate.title} "
                    f"[{candidate.granularity} #{candidate.chunk_index}] "
                    f"chunk={candidate.chunk_id} score={candidate.score:.6f} "
                    f"text={candidate.text_preview}"
                )
        lines.append(
            f"  context={profile.context_ms:.3f}ms "
            f"versions={profile.context_version_count} "
            f"chunks={profile.context_result_count}"
        )
        lines.append("  embeddings:")
        for embedding_call in profile.embedding_calls:
            lines.append(
                f"    embedding={embedding_call.elapsed_ms:9.3f}ms "
                f"inputs={embedding_call.input_count} "
                f"dim={embedding_call.dimension} "
                f"query={embedding_call.text}"
            )
        lines.append("  qdrant:")
        for vector_call in profile.vector_calls:
            lines.append(
                f"    qdrant={vector_call.elapsed_ms:9.3f}ms "
                f"limit={vector_call.limit} hits={vector_call.hit_count}"
            )
        lines.append(f"  titles={list(profile.result_titles)}")
        lines.append("  final items:")
        for candidate in profile.final_items:
            lines.append(
                f"    #{candidate.rank} {candidate.title} "
                f"[{candidate.granularity} #{candidate.chunk_index}] "
                f"chunk={candidate.chunk_id} score={candidate.score:.6f} "
                f"text={candidate.text_preview}"
            )
    return "\n".join(lines)


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _console_safe(text: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="backslashreplace").decode(encoding)


def _gold_match_ranks(
    items: list[Any],
    *,
    gold_evidence: tuple[Any, ...],
) -> tuple[int | None, ...]:
    from app.evaluation.retrieval import matches_selector

    return tuple(
        next(
            (
                rank
                for rank, item in enumerate(items, start=1)
                if matches_selector(item, selector)
            ),
            None,
        )
        for selector in gold_evidence
    )


def _candidate_snapshots(
    items: list[Any],
    *,
    limit: int,
) -> tuple[CandidateSnapshot, ...]:
    return tuple(
        CandidateSnapshot(
            rank=rank,
            chunk_id=item.chunk_id,
            poem_id=item.poem_id,
            poem_version_id=item.poem_version_id,
            title=item.title,
            granularity=item.granularity.value,
            chunk_index=item.chunk_index,
            score=item.score,
            match_types=tuple(item.match_types),
            text_preview=_text_preview(item.text),
        )
        for rank, item in enumerate(items[:limit], start=1)
    )


def _text_preview(text: str, *, limit: int = 80) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the online expanded-hybrid retrieval stack without "
            "changing its ranking policy."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=DEFAULT_BASELINE_REPORT,
        help="Report whose failure_case_ids are profiled by default.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Case id to profile. Repeat to select multiple cases.",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json-output", type=Path, default=None)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")

    profiles = asyncio.run(
        run_profile(
            args.dataset,
            args.baseline_report,
            args.case_id,
            top_k=args.top_k,
        )
    )
    print(_console_safe(format_profiles(profiles)))
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(
                [asdict(profile) for profile in profiles],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(_console_safe(f"\njson_output={args.json_output}"))


if __name__ == "__main__":
    main()
