from __future__ import annotations

from typing import Any

import pytest
from app.core.text import normalize_content, sha256_text
from app.models.annotation import AnnotationStatus, AnnotationType, PoemAnnotation
from app.models.chunk import ChunkGranularity, ChunkStatus, PoemChunk
from app.models.source import PoemSource
from app.models.version import PoemVersion, PoemVersionChangeType
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_catalog import _admin_client


async def _load_versions(
    session_factory: async_sessionmaker[AsyncSession],
    poem_id: int,
) -> list[PoemVersion]:
    async with session_factory() as session:
        result = await session.execute(
            select(PoemVersion)
            .where(PoemVersion.poem_id == poem_id)
            .order_by(PoemVersion.version_no)
        )
        return list(result.scalars())


async def _create_annotation_and_chunk(
    session_factory: async_sessionmaker[AsyncSession],
    poem_id: int,
    version_id: int,
) -> tuple[int, int]:
    async with session_factory() as session:
        annotation = PoemAnnotation(
            poem_version_id=version_id,
            annotation_type=AnnotationType.APPRECIATION.value,
            title="赏析",
            content="月光与思乡构成主要意境。",
            normalized_content=normalize_content("月光与思乡构成主要意境。"),
            line_start=1,
            line_end=2,
            status=AnnotationStatus.PUBLISHED.value,
            content_hash=sha256_text("月光与思乡构成主要意境。"),
        )
        session.add(annotation)
        await session.flush()
        chunk = PoemChunk(
            poem_id=poem_id,
            poem_version_id=version_id,
            annotation_id=annotation.id,
            granularity=ChunkGranularity.NOTE.value,
            chunk_index=0,
            text=annotation.content,
            normalized_text=annotation.normalized_content,
            content_hash=annotation.content_hash,
            line_start=1,
            line_end=2,
            chunk_strategy="structural-v1",
            status=ChunkStatus.READY.value,
            vector_id="test-note-chunk-1",
            embedding_model="test-embedding",
            embedding_dimension=8,
        )
        session.add(chunk)
        await session.commit()
        return annotation.id, chunk.id


def _create_catalog_poem(client: TestClient) -> tuple[dict[str, str], dict[str, Any]]:
    headers, _ = _admin_client(client)
    dynasty = client.post(
        "/api/v1/admin/dynasties",
        headers=headers,
        json={"name": "Tang"},
    ).json()["data"]
    author = client.post(
        "/api/v1/admin/authors",
        headers=headers,
        json={"name": "Li Bai", "dynasty_id": dynasty["id"]},
    ).json()["data"]
    poem = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Quiet Night",
            "author_id": author["id"],
            "dynasty_id": dynasty["id"],
            "content": "Moonlight before my bed.\nI lower my head and think of home.",
            "summary": "A traveler sees moonlight and remembers home.",
        },
    ).json()["data"]
    return headers, poem


def test_catalog_writes_immutable_version_chain(client: TestClient) -> None:
    headers, poem = _create_catalog_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    client.patch(
        f"/api/v1/admin/poems/{poem['id']}",
        headers=headers,
        json={"content": "Edited moonlight.", "version_no": 1},
    )
    client.post(f"/api/v1/admin/poems/{poem['id']}/publish", headers=headers)
    client.post(f"/api/v1/admin/poems/{poem['id']}/unpublish", headers=headers)
    client.delete(f"/api/v1/admin/poems/{poem['id']}", headers=headers)
    client.post(f"/api/v1/admin/poems/{poem['id']}/restore", headers=headers)

    versions = portal.call(_load_versions, session_factory, poem["id"])

    assert [version.version_no for version in versions] == [1, 2, 3, 4]
    assert [version.change_type for version in versions] == [
        PoemVersionChangeType.CREATE.value,
        PoemVersionChangeType.UPDATE.value,
        PoemVersionChangeType.ARCHIVE.value,
        PoemVersionChangeType.RESTORE.value,
    ]
    assert all(version.source_id == versions[0].source_id for version in versions)
    assert versions[0].source_id is not None
    assert versions[0].snapshot["content"] == poem["content"]
    assert versions[1].snapshot["content"] == "Edited moonlight."
    assert len({version.content_hash for version in versions}) == 4


def test_manual_source_is_created_with_poem(client: TestClient) -> None:
    _, poem = _create_catalog_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def load_source() -> PoemSource | None:
        async with session_factory() as session:
            return await session.scalar(
                select(PoemSource).where(PoemSource.poem_id == poem["id"])
            )

    source = portal.call(load_source)

    assert source is not None
    assert source.source_type == "manual"
    assert source.source_key == "manual"
    assert source.raw_content == poem["content"]
    assert source.content_hash == sha256_text(poem["content"])


def test_annotation_and_chunk_relations_and_unique_constraint(client: TestClient) -> None:
    _, poem = _create_catalog_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])
    annotation_id, chunk_id = portal.call(
        _create_annotation_and_chunk,
        session_factory,
        poem["id"],
        versions[0].id,
    )

    async def load_relations() -> tuple[PoemAnnotation, PoemChunk, int, int]:
        async with session_factory() as session:
            annotation = await session.get(PoemAnnotation, annotation_id)
            chunk = await session.get(PoemChunk, chunk_id)
            assert annotation is not None
            assert chunk is not None
            await session.refresh(annotation, attribute_names=["chunks"])
            await session.refresh(chunk, attribute_names=["version"])
            return annotation, chunk, annotation.chunks[0].id, chunk.version.id

    annotation, chunk, related_chunk_id, related_version_id = portal.call(load_relations)

    assert annotation.poem_version_id == versions[0].id
    assert chunk.annotation_id == annotation.id
    assert chunk.poem_id == poem["id"]
    assert chunk.granularity == ChunkGranularity.NOTE.value
    assert chunk.status == ChunkStatus.READY.value
    assert related_chunk_id == chunk.id
    assert related_version_id == versions[0].id

    async def insert_duplicate_chunk() -> None:
        async with session_factory() as session:
            session.add(
                PoemChunk(
                    poem_id=poem["id"],
                    poem_version_id=versions[0].id,
                    annotation_id=annotation.id,
                    granularity=ChunkGranularity.NOTE.value,
                    chunk_index=0,
                    text="duplicate",
                    normalized_text="duplicate",
                    content_hash=sha256_text("duplicate"),
                    chunk_strategy="structural-v1",
                    status=ChunkStatus.PENDING.value,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    portal.call(insert_duplicate_chunk)


def test_version_number_is_unique_per_poem(client: TestClient) -> None:
    _, poem = _create_catalog_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def insert_duplicate_version() -> None:
        async with session_factory() as session:
            session.add(
                PoemVersion(
                    poem_id=poem["id"],
                    source_id=versions[0].source_id,
                    version_no=versions[0].version_no,
                    snapshot=versions[0].snapshot,
                    content_hash=versions[0].content_hash,
                    change_type=PoemVersionChangeType.BACKFILL.value,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    portal.call(insert_duplicate_version)


def test_dynasty_and_wildcard_search(client: TestClient) -> None:
    headers, _ = _admin_client(client)
    dynasty = client.post(
        "/api/v1/admin/dynasties",
        headers=headers,
        json={"name": "Tang"},
    ).json()["data"]
    poem = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Literal 100%_poem",
            "dynasty_id": dynasty["id"],
            "content": "A title containing SQL wildcard characters.",
        },
    ).json()["data"]
    client.post(f"/api/v1/admin/poems/{poem['id']}/publish", headers=headers)

    dynasty_results = client.get("/api/v1/search", params={"q": "Tang"}).json()
    assert dynasty_results["meta"]["total"] == 1
    assert dynasty_results["data"][0]["id"] == poem["id"]

    wildcard_results = client.get("/api/v1/search", params={"q": "100%_poem"}).json()
    assert wildcard_results["meta"]["total"] == 1

    underscore_wildcard = client.get("/api/v1/search", params={"q": "100X_poem"}).json()
    assert underscore_wildcard["meta"]["total"] == 0
