from __future__ import annotations

from typing import Any

import pytest
from app.core.errors import AppError, ErrorCode
from app.core.text import normalize_content, sha256_text
from app.models.annotation import AnnotationStatus, AnnotationType, PoemAnnotation
from app.models.chunk import ChunkGranularity, ChunkStatus
from fastapi.testclient import TestClient
from test_catalog import _admin_client
from test_rag_corpus import _load_versions


def _create_poem(client: TestClient) -> tuple[dict[str, str], dict[str, Any]]:
    headers, _ = _admin_client(client)
    poem = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Quiet Night",
            "content": "Moonlight before my bed.\nI lower my head and think of home.",
            "summary": "A traveler sees moonlight.",
        },
    ).json()["data"]
    return headers, poem


def test_rebuild_version_chunks_is_idempotent_and_uses_published_annotations(
    client: TestClient,
) -> None:
    _, poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def create_annotations() -> tuple[int, int]:
        async with session_factory() as session:
            published = PoemAnnotation(
                poem_version_id=versions[0].id,
                annotation_type=AnnotationType.APPRECIATION.value,
                title="Published appreciation",
                content="Published analysis.",
                normalized_content=normalize_content("Published analysis."),
                status=AnnotationStatus.PUBLISHED.value,
                content_hash=sha256_text("Published analysis."),
            )
            draft = PoemAnnotation(
                poem_version_id=versions[0].id,
                annotation_type=AnnotationType.NOTE.value,
                title="Draft note",
                content="Draft note that must not be indexed.",
                normalized_content=normalize_content(
                    "Draft note that must not be indexed."
                ),
                status=AnnotationStatus.DRAFT.value,
                content_hash=sha256_text("Draft note that must not be indexed."),
            )
            session.add_all([published, draft])
            await session.commit()
            return published.id, draft.id

    published_id, _ = portal.call(create_annotations)
    from app.services.chunk_catalog import ChunkCatalogService

    async def rebuild() -> list[dict[str, Any]]:
        async with session_factory() as session:
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            return [
                {
                    "granularity": chunk.granularity,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    "annotation_id": chunk.annotation_id,
                    "status": chunk.status,
                }
                for chunk in chunks
            ]

    first = portal.call(rebuild)
    second = portal.call(rebuild)

    assert first == second
    assert [item["granularity"] for item in first] == [
        ChunkGranularity.POEM.value,
        ChunkGranularity.LINE.value,
        ChunkGranularity.LINE.value,
        ChunkGranularity.NOTE.value,
    ]
    assert first[-1]["annotation_id"] == published_id
    assert all(item["status"] == ChunkStatus.PENDING.value for item in first)


def test_rebuild_rejects_chunks_with_vectors(client: TestClient) -> None:
    _, poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def rebuild() -> None:
        from app.services.chunk_catalog import ChunkCatalogService

        async with session_factory() as session:
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            chunks[0].vector_id = "existing-vector"
            await session.commit()
            await ChunkCatalogService(session).rebuild_version_chunks(versions[0].id)

    with pytest.raises(AppError) as exc_info:
        portal.call(rebuild)
    assert exc_info.value.code == ErrorCode.CHUNKS_ALREADY_INDEXED
