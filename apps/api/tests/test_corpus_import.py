from __future__ import annotations

from typing import Any

import pytest
from app.models.annotation import AnnotationStatus, PoemAnnotation
from app.models.poem import Poem, PoemCategory
from app.models.source import PoemSource
from app.models.tag import PoemTag
from app.models.version import PoemVersion, PoemVersionChangeType
from app.schemas.corpus_import import CorpusImportDataset
from app.services.corpus_import import CorpusImportService
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload


def _dataset(
    *,
    content: str = "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。",
    publish: bool | None = None,
    include_annotation: bool = True,
) -> CorpusImportDataset:
    record: dict[str, Any] = {
        "external_id": "quiet-night-001",
        "title": "静夜思",
        "content": content,
        "author_name": "李白",
        "dynasty_name": "唐",
        "summary": "明月照入客居，抬头与低头之间，尽是故乡。",
        "categories": [
            {"name": "诗", "type": "work_type"},
            {"name": "五言绝句", "type": "form"},
        ],
        "tags": ["明月", "思乡"],
        "raw_payload": {"fixture": "open-licensed-corpus"},
    }
    if publish is not None:
        record["publish"] = publish
    if include_annotation:
        record["annotations"] = [
            {
                "type": "appreciation",
                "title": "赏析",
                "content": "月光与思乡构成主要意境。",
                "line_start": 1,
                "line_end": 2,
                "status": "published",
            }
        ]
    return CorpusImportDataset.model_validate(
        {
            "version": "fixture-v1",
            "source": {
                "source_key": "open-poetry-fixture",
                "source_type": "file",
                "source_name": "开放许可测试语料",
                "source_url": "https://example.com/open-poetry",
                "license_note": "仅用于测试的固定许可说明",
            },
            "defaults": {"publish": False},
            "records": [record],
        }
    )


async def _import(
    session_factory: async_sessionmaker[AsyncSession],
    dataset: CorpusImportDataset,
) -> Any:
    async with session_factory() as session:
        return await CorpusImportService(session).import_dataset(dataset)


async def _load_poem_graph(
    session_factory: async_sessionmaker[AsyncSession],
    poem_id: int,
) -> tuple[Poem, list[PoemSource], list[PoemVersion], list[PoemAnnotation]]:
    async with session_factory() as session:
        poem = await session.scalar(
            select(Poem)
            .where(Poem.id == poem_id)
            .options(
                selectinload(Poem.author),
                selectinload(Poem.dynasty),
                selectinload(Poem.category_links).selectinload(PoemCategory.category),
                selectinload(Poem.tag_links).selectinload(PoemTag.tag),
            )
        )
        assert poem is not None
        sources = list(
            (
                await session.execute(
                    select(PoemSource)
                    .where(PoemSource.poem_id == poem_id)
                    .order_by(PoemSource.id)
                )
            )
            .scalars()
            .all()
        )
        versions = list(
            (
                await session.execute(
                    select(PoemVersion)
                    .where(PoemVersion.poem_id == poem_id)
                    .order_by(PoemVersion.version_no)
                )
            )
            .scalars()
            .all()
        )
        annotations = list(
            (
                await session.execute(
                    select(PoemAnnotation).order_by(PoemAnnotation.id)
                )
            )
            .scalars()
            .all()
        )
        return poem, sources, versions, annotations


def test_import_creates_traceable_corpus_record(client: TestClient) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    report = portal.call(_import, session_factory, _dataset())

    assert report.total_records == 1
    assert report.created_records == 1, report.records[0].message
    result = report.records[0]
    assert result.status == "created"
    assert result.poem_id is not None
    assert result.version_id is not None
    assert result.version_no == 1

    poem, sources, versions, annotations = portal.call(
        _load_poem_graph,
        session_factory,
        result.poem_id,
    )
    assert poem.status == "draft"
    assert poem.author is not None and poem.author.name == "李白"
    assert poem.dynasty is not None and poem.dynasty.name == "唐"
    assert {link.category.name for link in poem.category_links} == {"诗", "五言绝句"}
    assert {link.tag.name for link in poem.tag_links} == {"明月", "思乡"}
    assert len(sources) == 1
    assert sources[0].source_key == "open-poetry-fixture"
    assert sources[0].external_id == "quiet-night-001"
    assert sources[0].license_note == "仅用于测试的固定许可说明"
    assert sources[0].raw_payload == {"fixture": "open-licensed-corpus"}
    assert len(versions) == 1
    assert versions[0].change_type == PoemVersionChangeType.IMPORT.value
    assert len(annotations) == 1
    assert annotations[0].poem_version_id == versions[0].id
    assert annotations[0].status == AnnotationStatus.PUBLISHED.value


def test_import_is_idempotent_and_preserves_published_status(
    client: TestClient,
) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    first = portal.call(_import, session_factory, _dataset(publish=True))
    assert first.created_records == 1
    poem_id = first.records[0].poem_id
    assert poem_id is not None

    second = portal.call(_import, session_factory, _dataset(publish=False))

    assert second.unchanged_records == 1
    assert second.updated_records == 0
    poem, sources, versions, annotations = portal.call(
        _load_poem_graph,
        session_factory,
        poem_id,
    )
    assert poem.status == "published"
    assert poem.version_no == 1
    assert len(sources) == 1
    assert len(versions) == 1
    assert len(annotations) == 1


def test_import_update_creates_new_version_and_keeps_history(
    client: TestClient,
) -> None:
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    first = portal.call(_import, session_factory, _dataset())
    poem_id = first.records[0].poem_id
    assert poem_id is not None
    first_version_id = first.records[0].version_id
    assert first_version_id is not None

    changed_content = "床前明月光，疑是地上霜。\n举头望明月，低头思故园。"
    second = portal.call(_import, session_factory, _dataset(content=changed_content))

    assert second.updated_records == 1
    assert second.records[0].version_no == 2
    assert second.records[0].version_id != first_version_id
    poem, sources, versions, annotations = portal.call(
        _load_poem_graph,
        session_factory,
        poem_id,
    )
    assert poem.content == changed_content
    assert poem.version_no == 2
    assert len(sources) == 1
    assert [version.version_no for version in versions] == [1, 2]
    assert all(version.change_type == PoemVersionChangeType.IMPORT.value for version in versions)
    assert versions[0].snapshot["content"] != versions[1].snapshot["content"]
    assert len(annotations) == 2
    assert {annotation.poem_version_id for annotation in annotations} == {
        versions[0].id,
        versions[1].id,
    }


def test_import_isolates_bad_record(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _dataset()
    bad_record = dataset.records[0].model_copy(
        update={"external_id": "bad-dynasty", "dynasty_name": "Broken"}
    )
    dataset = dataset.model_copy(update={"records": [bad_record, dataset.records[0]]})
    original = CorpusImportService._resolve_dynasty

    async def fail_for_broken(self: CorpusImportService, name: str | None):
        if name == "Broken":
            raise RuntimeError("synthetic row failure")
        return await original(self, name)

    monkeypatch.setattr(CorpusImportService, "_resolve_dynasty", fail_for_broken)
    session_factory = client.app.state.session_factory
    portal = client.portal
    assert portal is not None
    report = portal.call(_import, session_factory, dataset)

    assert report.failed_records == 1
    assert report.created_records == 1
    assert [item.status for item in report.records] == ["failed", "created"]
    assert "synthetic row failure" in (report.records[0].message or "")


def test_import_schema_rejects_duplicate_external_ids() -> None:
    dataset = _dataset()
    payload = dataset.model_dump(mode="json")
    payload["records"].append(payload["records"][0])

    with pytest.raises(ValidationError, match="external_id"):
        CorpusImportDataset.model_validate(payload)


def test_import_schema_normalizes_and_limits_tags() -> None:
    payload = _dataset().model_dump(mode="json")
    payload["records"][0]["tags"] = ["明月", " 明月 ", "Moon", "moon"]

    dataset = CorpusImportDataset.model_validate(payload)

    assert dataset.records[0].tags == ["明月", "Moon"]

    payload["records"][0]["tags"] = ["x" * 81]
    with pytest.raises(ValidationError):
        CorpusImportDataset.model_validate(payload)
