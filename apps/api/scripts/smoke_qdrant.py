from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

_POINT_A = "00000000-0000-0000-0000-000000000001"
_POINT_B = "00000000-0000-0000-0000-000000000002"


@dataclass(frozen=True, slots=True)
class QdrantSmokeResult:
    url: str
    collection: str
    dimension: int
    first_hit_id: str
    deleted_hit_count: int


async def run_smoke(
    *,
    url: str,
    dimension: int = 8,
    collection_prefix: str = "poem_chunks_smoke",
) -> QdrantSmokeResult:
    from app.ai.providers.qdrant import QdrantVectorStore, VectorStoreError
    from app.ai.providers.vector_store import VectorPoint, VectorSearchRequest
    from qdrant_client import AsyncQdrantClient

    if dimension < 2:
        raise ValueError("dimension 必须至少为 2")

    collection_name = _temporary_collection_name(collection_prefix)
    client = AsyncQdrantClient(
        url=url,
        timeout=10,
        check_compatibility=False,
    )
    store = QdrantVectorStore(
        url=url,
        collection=collection_name,
        timeout_seconds=10,
        client=client,
    )
    try:
        await store.ensure_collection(dimension=dimension)
        try:
            await store.ensure_collection(dimension=dimension + 1)
        except VectorStoreError:
            pass
        else:
            raise RuntimeError("Collection 维度校验没有拒绝不匹配维度")

        await store.upsert(
            [
                VectorPoint(
                    id=_POINT_A,
                    vector=[1.0, *([0.0] * (dimension - 1))],
                    payload={
                        "chunk_id": 1,
                        "granularity": "line",
                        "author_id": 7,
                        "dynasty_id": 3,
                        "chunk_strategy": "structural-v1",
                    },
                ),
                VectorPoint(
                    id=_POINT_B,
                    vector=[0.0, 1.0, *([0.0] * (dimension - 2))],
                    payload={
                        "chunk_id": 2,
                        "granularity": "poem",
                        "author_id": 8,
                        "dynasty_id": 4,
                        "chunk_strategy": "structural-v1",
                    },
                ),
            ]
        )

        hits = await store.search(
            VectorSearchRequest(
                vector=[1.0, *([0.0] * (dimension - 1))],
                limit=5,
                granularities=("line",),
                author_id=7,
                dynasty_id=3,
                chunk_strategy="structural-v1",
            )
        )
        if not hits or hits[0].id != _POINT_A:
            raise RuntimeError(f"过滤检索未返回预期命中: {hits!r}")

        await store.delete([_POINT_A])
        remaining = await store.search(
            VectorSearchRequest(
                vector=[1.0, *([0.0] * (dimension - 1))],
                limit=5,
                chunk_strategy="structural-v1",
            )
        )
        deleted_hit_count = sum(hit.id == _POINT_A for hit in remaining)
        if deleted_hit_count != 0:
            raise RuntimeError("删除后仍能检索到测试向量")

        return QdrantSmokeResult(
            url=url,
            collection=collection_name,
            dimension=dimension,
            first_hit_id=hits[0].id,
            deleted_hit_count=deleted_hit_count,
        )
    finally:
        try:
            if await client.collection_exists(collection_name):
                await client.delete_collection(collection_name)
        finally:
            await client.close()


def _temporary_collection_name(prefix: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", prefix).strip("_")
    if not normalized:
        raise ValueError("collection prefix 至少包含一个字母、数字、下划线或连字符")
    return f"{normalized[:80]}_{uuid.uuid4().hex[:12]}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a destructive-safe Qdrant adapter smoke test in a temporary collection."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:6333",
        help="Qdrant HTTP URL. Default: http://127.0.0.1:6333",
    )
    parser.add_argument(
        "--dimension",
        type=int,
        default=8,
        help="Deterministic smoke-test vector dimension. Default: 8",
    )
    parser.add_argument(
        "--collection-prefix",
        default="poem_chunks_smoke",
        help="Prefix for the generated temporary collection. Default: poem_chunks_smoke",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(
            run_smoke(
                url=args.url,
                dimension=args.dimension,
                collection_prefix=args.collection_prefix,
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Qdrant smoke failed: {exc}\n")

    print(
        f"Qdrant smoke passed: url={result.url} collection={result.collection} "
        f"dimension={result.dimension} first_hit={result.first_hit_id} "
        f"deleted_hit_count={result.deleted_hit_count}"
    )


if __name__ == "__main__":
    main()
