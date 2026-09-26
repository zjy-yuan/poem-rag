from __future__ import annotations

from typing import Any

from app.core.text import normalize_content, sha256_text
from app.models.annotation import AnnotationStatus, AnnotationType, PoemAnnotation
from app.models.chunk import ChunkStatus
from app.repositories.chunks import ChunkRepository
from app.services.chunk_catalog import ChunkCatalogService
from fastapi.testclient import TestClient
from test_catalog import _admin_client
from test_rag_corpus import _load_versions


def _create_catalog(
    client: TestClient,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    headers, _ = _admin_client(client)
    dynasty = client.post(
        "/api/v1/admin/dynasties",
        headers=headers,
        json={"name": "唐"},
    ).json()["data"]
    author = client.post(
        "/api/v1/admin/authors",
        headers=headers,
        json={"name": "李白", "dynasty_id": dynasty["id"]},
    ).json()["data"]
    return headers, dynasty, author


def _create_poem(
    client: TestClient,
    headers: dict[str, str],
    *,
    title: str,
    content: str,
    author_id: int | None = None,
    dynasty_id: int | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": title,
            "content": content,
            "author_id": author_id,
            "dynasty_id": dynasty_id,
        },
    )
    assert response.status_code == 201
    return response.json()["data"]


def _publish(client: TestClient, headers: dict[str, str], poem_id: int) -> None:
    response = client.post(f"/api/v1/admin/poems/{poem_id}/publish", headers=headers)
    assert response.status_code == 200


def _rebuild_chunks(client: TestClient, version_id: int) -> None:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def rebuild() -> None:
        async with session_factory() as session:
            await ChunkCatalogService(session).rebuild_version_chunks(version_id)

    portal.call(rebuild)


def _add_published_annotation(
    client: TestClient,
    *,
    poem_version_id: int,
    content: str,
) -> int:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def create() -> int:
        async with session_factory() as session:
            annotation = PoemAnnotation(
                poem_version_id=poem_version_id,
                annotation_type=AnnotationType.APPRECIATION.value,
                title="赏析",
                content=content,
                normalized_content=normalize_content(content),
                line_start=1,
                line_end=2,
                status=AnnotationStatus.PUBLISHED.value,
                content_hash=sha256_text(content),
            )
            session.add(annotation)
            await session.commit()
            return annotation.id

    return portal.call(create)


def _disable_chunks(client: TestClient, poem_version_id: int) -> None:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def disable() -> None:
        from app.models.chunk import PoemChunk
        from sqlalchemy import update

        async with session_factory() as session:
            await session.execute(
                update(PoemChunk)
                .where(PoemChunk.poem_version_id == poem_version_id)
                .values(status=ChunkStatus.DISABLED.value)
            )
            await session.commit()

    portal.call(disable)


def test_evidence_search_returns_line_poem_and_note_matches(client: TestClient) -> None:
    headers, dynasty, author = _create_catalog(client)
    content = "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。"
    poem = _create_poem(
        client,
        headers,
        title="静夜思",
        content=content,
        author_id=author["id"],
        dynasty_id=dynasty["id"],
    )
    _publish(client, headers, poem["id"])
    portal = client.portal
    assert portal is not None
    versions = portal.call(
        _load_versions,
        client.app.state.session_factory,
        poem["id"],
    )
    _add_published_annotation(
        client,
        poem_version_id=versions[0].id,
        content="月光与思乡构成主要意境。",
    )
    _rebuild_chunks(client, versions[0].id)

    line_response = client.get(
        "/api/v1/search/evidence",
        params={"q": "床前明月光，疑是地上霜。", "limit": 5},
    )
    assert line_response.status_code == 200
    line_body = line_response.json()
    assert line_body["meta"]["strategy"] == "lexical-baseline-v1"
    assert line_body["data"][0]["granularity"] == "line"
    assert line_body["data"][0]["text"] == "床前明月光，疑是地上霜。"
    assert line_body["data"][0]["score"] == 1.0
    assert line_body["data"][0]["match_types"] == ["chunk_exact"]

    note_response = client.get(
        "/api/v1/search/evidence",
        params={"q": "月光与思乡", "granularity": "note"},
    )
    assert note_response.status_code == 200
    note = note_response.json()["data"][0]
    assert note["granularity"] == "note"
    assert note["annotation_type"] == AnnotationType.APPRECIATION.value
    assert note["text"] == "月光与思乡构成主要意境。"
    assert "chunk_phrase" in note["match_types"]

    title_response = client.get(
        "/api/v1/search/evidence",
        params={"q": "静夜思", "author_id": author["id"]},
    )
    title_items = title_response.json()["data"]
    assert title_items
    assert all(item["poem_id"] == poem["id"] for item in title_items)
    assert all("title_phrase" in item["match_types"] for item in title_items)
    assert [item["granularity"] for item in title_items[:3]] == [
        "line",
        "line",
        "poem",
    ]


def test_evidence_search_excludes_draft_deleted_disabled_and_old_versions(
    client: TestClient,
) -> None:
    headers, _, _ = _create_catalog(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    draft = _create_poem(client, headers, title="草稿", content="草稿专属诗句。")
    draft_versions = portal.call(_load_versions, session_factory, draft["id"])
    _rebuild_chunks(client, draft_versions[0].id)
    assert (
        client.get("/api/v1/search/evidence", params={"q": "草稿专属诗句"}).json()["data"]
        == []
    )

    deleted = _create_poem(client, headers, title="已删除", content="删除后不可检索。")
    _publish(client, headers, deleted["id"])
    deleted_versions = portal.call(_load_versions, session_factory, deleted["id"])
    _rebuild_chunks(client, deleted_versions[0].id)
    delete_response = client.delete(
        f"/api/v1/admin/poems/{deleted['id']}",
        headers=headers,
    )
    assert delete_response.status_code == 200
    assert (
        client.get("/api/v1/search/evidence", params={"q": "删除后不可检索"}).json()["data"]
        == []
    )

    disabled = _create_poem(client, headers, title="禁用", content="禁用片段不可检索。")
    _publish(client, headers, disabled["id"])
    disabled_versions = portal.call(_load_versions, session_factory, disabled["id"])
    _rebuild_chunks(client, disabled_versions[0].id)
    _disable_chunks(client, disabled_versions[0].id)
    assert (
        client.get("/api/v1/search/evidence", params={"q": "禁用片段不可检索"}).json()["data"]
        == []
    )

    updated = _create_poem(client, headers, title="旧版本", content="旧版本专属诗句。")
    _publish(client, headers, updated["id"])
    updated_versions = portal.call(_load_versions, session_factory, updated["id"])
    _rebuild_chunks(client, updated_versions[0].id)
    update_response = client.patch(
        f"/api/v1/admin/poems/{updated['id']}",
        headers=headers,
        json={"content": "新版本专属诗句。", "version_no": 1},
    )
    assert update_response.status_code == 200
    assert (
        client.get("/api/v1/search/evidence", params={"q": "旧版本专属诗句"}).json()["data"]
        == []
    )


def test_evidence_search_escapes_wildcards_and_rejects_blank_query(
    client: TestClient,
) -> None:
    headers, _, _ = _create_catalog(client)
    poem = _create_poem(
        client,
        headers,
        title="Wildcard",
        content="Literal 100%_line remains literal.",
    )
    _publish(client, headers, poem["id"])
    portal = client.portal
    assert portal is not None
    versions = portal.call(
        _load_versions,
        client.app.state.session_factory,
        poem["id"],
    )
    _rebuild_chunks(client, versions[0].id)

    exact = client.get(
        "/api/v1/search/evidence",
        params={"q": "100%_line"},
    )
    assert exact.status_code == 200
    assert exact.json()["data"][0]["text"] == "Literal 100%_line remains literal."

    wildcard = client.get(
        "/api/v1/search/evidence",
        params={"q": "100X_line"},
    )
    assert wildcard.status_code == 200
    assert wildcard.json()["data"] == []

    blank = client.get("/api/v1/search/evidence", params={"q": "   "})
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "VALIDATION_ERROR"


def test_lexical_candidate_selection_prioritizes_title_over_earlier_body_match(
    client: TestClient,
) -> None:
    headers, dynasty, author = _create_catalog(client)
    filler = _create_poem(
        client,
        headers,
        title="秋日",
        content="登高望远，天地苍茫。",
        author_id=author["id"],
        dynasty_id=dynasty["id"],
    )
    target = _create_poem(
        client,
        headers,
        title="登高",
        content="风急天高猿啸哀。",
        author_id=author["id"],
        dynasty_id=dynasty["id"],
    )
    _publish(client, headers, filler["id"])
    _publish(client, headers, target["id"])

    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    for poem in (filler, target):
        versions = portal.call(_load_versions, session_factory, poem["id"])
        _rebuild_chunks(client, versions[0].id)

    async def search() -> list[str]:
        async with session_factory() as session:
            candidates = await ChunkRepository(session).search_lexical(
                query="登高",
                limit=1,
            )
            return [candidate.title for candidate in candidates]

    assert portal.call(search) == ["登高"]


def test_evidence_search_prioritizes_exact_title_over_body_substring(
    client: TestClient,
) -> None:
    headers, dynasty, author = _create_catalog(client)
    filler = _create_poem(
        client,
        headers,
        title="秋日",
        content="登高望远，天地苍茫。",
        author_id=author["id"],
        dynasty_id=dynasty["id"],
    )
    target = _create_poem(
        client,
        headers,
        title="登高",
        content="风急天高猿啸哀。",
        author_id=author["id"],
        dynasty_id=dynasty["id"],
    )
    _publish(client, headers, filler["id"])
    _publish(client, headers, target["id"])

    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    for poem in (filler, target):
        versions = portal.call(_load_versions, session_factory, poem["id"])
        _rebuild_chunks(client, versions[0].id)

    response = client.get("/api/v1/search/evidence", params={"q": "登高"})

    assert response.status_code == 200
    items = response.json()["data"]
    assert items[0]["poem_id"] == target["id"]
    assert items[0]["title"] == "登高"
    assert "title_phrase" in items[0]["match_types"]
