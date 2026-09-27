from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from app.core.config import Settings
from app.core.text import normalize_content, sha256_text
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.evaluation.domain_label_sampling import (
    DomainLabelSamplingPreparer,
    _selection_digest,
)
from app.models.author import Author
from app.models.domain_label import DomainLabel, PoemVersionDomainLabel
from app.models.dynasty import Dynasty
from app.models.poem import Poem, PoemStatus
from app.models.source import PoemSource
from app.models.version import PoemVersion
from app.schemas.domain_label_sampling import (
    DomainLabelSamplingManifest,
    DomainLabelSamplingStratum,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

SOURCE_KEY = "sampling-fixture"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SAMPLING_MANIFEST_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"
)
V1_GOLD_DATASET_PATH = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v1.json"
)


@pytest.fixture()
async def sampling_session(
    api_settings: Settings,
) -> AsyncIterator[AsyncSession]:
    import app.models  # noqa: F401

    engine = create_database_engine(api_settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _add_candidate(
    session: AsyncSession,
    *,
    external_id: str,
    title: str,
    content: str,
    author_name: str,
    dynasty_name: str,
    source_key: str = SOURCE_KEY,
    source_content_hash: str | None = None,
) -> PoemVersion:
    dynasty = await session.scalar(
        select(Dynasty).where(Dynasty.normalized_name == dynasty_name)
    )
    if dynasty is None:
        dynasty = Dynasty(
            name=dynasty_name,
            normalized_name=dynasty_name,
        )
        session.add(dynasty)
        await session.flush()

    author = Author(
        name=author_name,
        normalized_name=author_name,
        aliases=[],
    )
    session.add(author)
    await session.flush()

    poem = Poem(
        title=title,
        author_id=author.id,
        dynasty_id=dynasty.id,
        content=content,
        normalized_content=normalize_content(content),
        status=PoemStatus.PUBLISHED.value,
        version_no=1,
    )
    session.add(poem)
    await session.flush()

    source = PoemSource(
        poem_id=poem.id,
        source_type="file",
        source_key=source_key,
        source_name="采样测试语料",
        external_id=external_id,
        source_url=f"https://example.com/{external_id}",
        raw_title=title,
        raw_author_name=author_name,
        raw_dynasty_name=dynasty_name,
        raw_content=content,
        content_hash=source_content_hash or sha256_text(content),
    )
    session.add(source)
    await session.flush()

    version = PoemVersion(
        poem_id=poem.id,
        source_id=source.id,
        version_no=1,
        snapshot={"content": content},
        content_hash=sha256_text(content),
        change_type="import",
    )
    session.add(version)
    await session.flush()
    return version


async def _seed_sampling_corpus(session: AsyncSession) -> None:
    await _add_candidate(
        session,
        external_id="excluded-tang",
        title="排除作品",
        content="甲\n乙",
        author_name="排除作者",
        dynasty_name="唐",
    )
    for index in range(2):
        await _add_candidate(
            session,
            external_id=f"tang-{index}",
            title=f"唐代诗-{index}",
            content=f"唐句一-{index}\n唐句二-{index}",
            author_name=f"唐代作者-{index}",
            dynasty_name="唐",
        )
    for index in range(2):
        await _add_candidate(
            session,
            external_id=f"song-{index}",
            title=f"宋代作品-{index}",
            content=f"宋句一-{index}\n宋句二-{index}",
            author_name=f"宋代作者-{index}",
            dynasty_name="宋",
        )
    for index in range(2):
        await _add_candidate(
            session,
            external_id=f"other-{index}",
            title=f"其他朝代作品-{index}",
            content=f"明句一-{index}\n明句二-{index}",
            author_name=f"明代作者-{index}",
            dynasty_name="明",
        )
    for index in range(2):
        content = "\n".join(f"长句-{index}-{line}" for line in range(10))
        await _add_candidate(
            session,
            external_id=f"long-{index}",
            title=f"长文本作品-{index}",
            content=content,
            author_name=f"长文作者-{index}",
            dynasty_name="清",
        )
    await session.commit()


async def test_sampling_is_deterministic_stratified_and_label_blind(
    sampling_session: AsyncSession,
) -> None:
    await _seed_sampling_corpus(sampling_session)
    preparer = DomainLabelSamplingPreparer(sampling_session)

    first = await preparer.prepare(
        source_key=SOURCE_KEY,
        count=8,
        seed="fixed-seed",
        excluded_external_ids={"excluded-tang"},
    )
    selected = first.records[0]
    label = DomainLabel(
        dimension="imagery",
        canonical_name="测试月",
        normalized_name="测试月",
        status="active",
    )
    sampling_session.add(label)
    await sampling_session.flush()
    sampling_session.add(
        PoemVersionDomainLabel(
            poem_version_id=selected.version_id,
            domain_label_id=label.id,
            generation_method="ai",
            origin_ref="ai:sampling:test-month",
            review_status="approved",
            model_name="test-model",
            task_version="test-task",
        )
    )
    await sampling_session.commit()

    second = await preparer.prepare(
        source_key=SOURCE_KEY,
        count=8,
        seed="fixed-seed",
        excluded_external_ids={"excluded-tang"},
    )

    assert [record.external_id for record in first.records] == [
        record.external_id for record in second.records
    ]
    assert len(first.records) == 8
    assert "excluded-tang" not in {
        record.external_id for record in first.records
    }
    assert [record.stratum for record in first.records] == [
        DomainLabelSamplingStratum.TANG_POEM,
        DomainLabelSamplingStratum.TANG_POEM,
        DomainLabelSamplingStratum.SONG_WORK,
        DomainLabelSamplingStratum.SONG_WORK,
        DomainLabelSamplingStratum.OTHER_DYNASTY,
        DomainLabelSamplingStratum.OTHER_DYNASTY,
        DomainLabelSamplingStratum.LONG_TEXT_OR_CI,
        DomainLabelSamplingStratum.LONG_TEXT_OR_CI,
    ]
    assert all(record.line_numbered_content.startswith("1 | ") for record in first.records)
    assert all(
        record.line_count == len(record.line_numbered_content.splitlines())
        for record in first.records
    )
    assert all(record.manual_labels.is_empty() for record in first.records)
    assert first.excluded_external_ids == ["excluded-tang"]


async def test_sampling_rejects_source_content_hash_mismatch(
    sampling_session: AsyncSession,
) -> None:
    await _seed_sampling_corpus(sampling_session)
    source = await sampling_session.scalar(
        select(PoemSource).where(PoemSource.external_id == "tang-0")
    )
    assert source is not None
    source.content_hash = "0" * 64
    await sampling_session.commit()

    with pytest.raises(ValueError, match="内容哈希"):
        await DomainLabelSamplingPreparer(sampling_session).prepare(
            source_key=SOURCE_KEY,
            count=4,
            seed="fixed-seed",
            excluded_external_ids=set(),
        )


def test_sampling_schema_rejects_prefilled_or_inconsistent_labels() -> None:
    payload = {
        "version": "sampling-test-v1",
        "name": "采样测试",
        "description": "采样 Schema 测试",
        "selection_strategy": "固定测试样本",
        "source_key": SOURCE_KEY,
        "random_seed": "fixed-seed",
        "requested_count": 4,
        "sampled_count": 4,
        "generated_at": "2026-09-27T00:00:00Z",
        "excluded_external_ids": [],
        "records": [
            {
                "external_id": f"sample-{index}",
                "poem_id": index + 1,
                "version_id": index + 1,
                "title": f"作品-{index}",
                "author": f"作者-{index}",
                "dynasty": "唐",
                "content_hash": sha256_text(f"正文-{index}"),
                "line_count": 1,
                "line_numbered_content": f"1 | 正文-{index}",
                "stratum": "tang-poem",
                "annotation_status": "pending",
                "manual_labels": {
                    "imagery": [],
                    "emotion": [],
                    "theme": [],
                    "allusion": [],
                },
                "review_note": None,
            }
            for index in range(4)
        ],
    }
    payload["records"][0]["manual_labels"]["imagery"] = [
        {
            "name": "月",
            "severity": "normal",
            "note": None,
            "evidence": [],
        }
    ]
    with pytest.raises(ValidationError, match="不能预填人工标签"):
        DomainLabelSamplingManifest.model_validate(payload)

    payload["records"][0]["manual_labels"]["imagery"] = []
    payload["records"][0]["line_count"] = 2
    with pytest.raises(ValidationError, match="line_count"):
        DomainLabelSamplingManifest.model_validate(payload)


def test_frozen_blind_sampling_manifest_contract() -> None:
    payload = json.loads(SAMPLING_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = DomainLabelSamplingManifest.model_validate(payload)
    v1_payload = json.loads(V1_GOLD_DATASET_PATH.read_text(encoding="utf-8"))
    v1_external_ids = {
        record["external_id"] for record in v1_payload["records"]
    }

    assert manifest.version == "domain-label-gold-v2-sampling"
    assert manifest.source_key == "aopao-chinese-gushiwen"
    assert manifest.requested_count == 24
    assert manifest.sampled_count == 24
    assert manifest.excluded_external_ids == sorted(v1_external_ids)
    assert Counter(record.stratum for record in manifest.records) == {
        DomainLabelSamplingStratum.TANG_POEM: 6,
        DomainLabelSamplingStratum.SONG_WORK: 6,
        DomainLabelSamplingStratum.OTHER_DYNASTY: 6,
        DomainLabelSamplingStratum.LONG_TEXT_OR_CI: 6,
    }
    assert {
        record.external_id for record in manifest.records
    }.isdisjoint(v1_external_ids)
    assert all(record.manual_labels.is_empty() for record in manifest.records)
    assert max(Counter(record.author for record in manifest.records).values()) <= 2


def test_selection_digest_is_stable() -> None:
    assert _selection_digest("fixed-seed", "external-id") == _selection_digest(
        "fixed-seed",
        "external-id",
    )
    assert _selection_digest("fixed-seed", "a") != _selection_digest(
        "fixed-seed",
        "b",
    )
