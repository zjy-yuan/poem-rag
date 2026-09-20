from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize_content, normalize_lookup, sha256_text
from app.models.annotation import PoemAnnotation
from app.models.author import Author
from app.models.dynasty import Dynasty
from app.models.poem import Poem, PoemStatus
from app.models.source import PoemSource
from app.models.version import PoemVersion, PoemVersionChangeType
from app.repositories.authors import AuthorRepository
from app.repositories.categories import CategoryRepository
from app.repositories.dynasties import DynastyRepository
from app.repositories.poems import PoemRepository
from app.schemas.corpus_import import (
    CorpusImportAnnotation,
    CorpusImportDataset,
    CorpusImportRecord,
    CorpusImportRecordResult,
    CorpusImportReport,
    CorpusImportSource,
)
from app.services.catalog import CatalogService


class CorpusImportService:
    """Import structured, licensed corpus data with per-record failure isolation."""

    def __init__(self, session: AsyncSession, *, changed_by_id: int | None = None) -> None:
        self.session = session
        self.catalog = CatalogService(session, changed_by_id=changed_by_id)
        self.poems = PoemRepository(session)
        self.authors = AuthorRepository(session)
        self.dynasties = DynastyRepository(session)
        self.categories = CategoryRepository(session)

    async def import_dataset(self, dataset: CorpusImportDataset) -> CorpusImportReport:
        results: list[CorpusImportRecordResult] = []
        for record in dataset.records:
            try:
                async with self.session.begin_nested():
                    result = await self._import_record(dataset, record)
                await self.session.commit()
            except Exception as exc:
                await self.session.rollback()
                result = CorpusImportRecordResult(
                    external_id=record.external_id,
                    status="failed",
                    message=str(exc),
                )
            results.append(result)

        return CorpusImportReport(
            dataset_version=dataset.version,
            source_key=dataset.source.source_key,
            total_records=len(results),
            created_records=sum(item.status == "created" for item in results),
            updated_records=sum(item.status == "updated" for item in results),
            unchanged_records=sum(item.status == "unchanged" for item in results),
            failed_records=sum(item.status == "failed" for item in results),
            records=results,
        )

    async def _import_record(
        self,
        dataset: CorpusImportDataset,
        record: CorpusImportRecord,
    ) -> CorpusImportRecordResult:
        source = await self._find_source(dataset.source.source_key, record.external_id)
        if source is None:
            return await self._create_record(dataset, record)
        return await self._update_record(dataset, record, source)

    async def _create_record(
        self,
        dataset: CorpusImportDataset,
        record: CorpusImportRecord,
    ) -> CorpusImportRecordResult:
        author = await self._resolve_author(record.author_name)
        dynasty = await self._resolve_dynasty(record.dynasty_name)
        category_ids = await self._resolve_categories(record)

        poem = self.poems.create(
            title=record.title,
            content=record.content,
            normalized_content=normalize_content(record.content),
            summary=record.summary,
            author_id=author.id if author is not None else None,
            dynasty_id=dynasty.id if dynasty is not None else None,
        )
        await self.catalog.replace_categories(poem, category_ids, source=dataset.source.source_type)
        await self.catalog.replace_tags(poem, record.tags)
        await self.session.flush()

        publish = dataset.defaults.publish if record.publish is None else record.publish
        self._publish_if_requested(poem, publish=publish)

        source = self.catalog.create_source(
            poem,
            source_type=dataset.source.source_type,
            source_key=dataset.source.source_key,
            source_name=dataset.source.source_name,
            external_id=record.external_id,
            source_url=record.source_url or dataset.source.source_url,
            raw_title=record.title,
            raw_author_name=record.author_name,
            raw_dynasty_name=record.dynasty_name,
            raw_content=record.content,
            raw_payload=record.raw_payload,
            content_hash=sha256_text(record.content),
            license_note=dataset.source.license_note,
        )
        self.session.add(source)
        await self.session.flush()

        version = await self.catalog.record_version(
            poem,
            change_type=PoemVersionChangeType.IMPORT,
            source_id=source.id,
        )
        await self._add_annotations(version.id, source.id, record.annotations)
        await self.session.flush()

        return CorpusImportRecordResult(
            external_id=record.external_id,
            status="created",
            poem_id=poem.id,
            version_id=version.id,
            version_no=version.version_no,
        )

    async def _update_record(
        self,
        dataset: CorpusImportDataset,
        record: CorpusImportRecord,
        source: PoemSource,
    ) -> CorpusImportRecordResult:
        poem = await self.poems.get_admin(source.poem_id)
        if poem is None:
            raise ValueError(f"来源关联的诗词不存在: poem_id={source.poem_id}")

        author = await self._resolve_author(record.author_name)
        dynasty = await self._resolve_dynasty(record.dynasty_name)
        category_ids = await self._resolve_categories(record)
        current_version = await self._get_current_version(poem)
        current_annotations = (
            await self._get_annotations(current_version.id) if current_version is not None else []
        )
        publish = dataset.defaults.publish if record.publish is None else record.publish

        if self._is_unchanged(
            poem=poem,
            record=record,
            source=source,
            dataset_source=dataset.source,
            author=author,
            dynasty=dynasty,
            category_ids=category_ids,
            current_annotations=current_annotations,
            publish=publish,
        ):
            return CorpusImportRecordResult(
                external_id=record.external_id,
                status="unchanged",
                poem_id=poem.id,
                version_id=current_version.id if current_version is not None else None,
                version_no=poem.version_no,
            )

        poem.title = record.title
        poem.content = record.content
        poem.normalized_content = normalize_content(record.content)
        poem.summary = record.summary
        poem.author_id = author.id if author is not None else None
        poem.dynasty_id = dynasty.id if dynasty is not None else None
        await self.catalog.replace_categories(poem, category_ids, source=dataset.source.source_type)
        await self.catalog.replace_tags(poem, record.tags)
        self._publish_if_requested(poem, publish=publish)
        self._update_source_metadata(
            source,
            record=record,
            dataset_source=dataset.source,
        )

        poem.version_no += 1
        await self.session.flush()
        version = await self.catalog.record_version(
            poem,
            change_type=PoemVersionChangeType.IMPORT,
            source_id=source.id,
        )
        await self._add_annotations(version.id, source.id, record.annotations)
        await self.session.flush()

        return CorpusImportRecordResult(
            external_id=record.external_id,
            status="updated",
            poem_id=poem.id,
            version_id=version.id,
            version_no=version.version_no,
        )

    async def _find_source(self, source_key: str, external_id: str) -> PoemSource | None:
        return await self.session.scalar(
            select(PoemSource).where(
                PoemSource.source_key == source_key,
                PoemSource.external_id == external_id,
            )
        )

    async def _resolve_author(self, name: str | None) -> Author | None:
        if name is None:
            return None
        normalized = normalize_lookup(name)
        author = await self.authors.get_by_normalized_name(normalized)
        if author is None:
            author = self.authors.create(name=name, normalized_name=normalized)
            await self.session.flush()
        return author

    async def _resolve_dynasty(self, name: str | None) -> Dynasty | None:
        if name is None:
            return None
        normalized = normalize_lookup(name)
        dynasty = await self.dynasties.get_by_normalized_name(normalized)
        if dynasty is None:
            dynasty = self.dynasties.create(name=name, normalized_name=normalized)
            await self.session.flush()
        return dynasty

    async def _resolve_categories(self, record: CorpusImportRecord) -> list[int]:
        category_ids: list[int] = []
        for item in record.categories:
            normalized = normalize_lookup(item.name)
            category = await self.categories.get_by_normalized_name(
                normalized,
                item.type.value,
            )
            if category is None:
                category = self.categories.create(
                    name=item.name,
                    normalized_name=normalized,
                    category_type=item.type.value,
                )
                await self.session.flush()
            category_ids.append(category.id)
        return category_ids

    async def _get_current_version(self, poem: Poem) -> PoemVersion | None:
        return await self.session.scalar(
            select(PoemVersion).where(
                PoemVersion.poem_id == poem.id,
                PoemVersion.version_no == poem.version_no,
            )
        )

    async def _get_annotations(self, version_id: int) -> list[PoemAnnotation]:
        result = await self.session.execute(
            select(PoemAnnotation)
            .where(PoemAnnotation.poem_version_id == version_id)
            .order_by(
                PoemAnnotation.annotation_type,
                PoemAnnotation.title,
                PoemAnnotation.id,
            )
        )
        return list(result.scalars())

    async def _add_annotations(
        self,
        version_id: int,
        source_id: int,
        annotations: list[CorpusImportAnnotation],
    ) -> None:
        for item in annotations:
            self.session.add(
                PoemAnnotation(
                    poem_version_id=version_id,
                    source_id=source_id,
                    annotation_type=item.type.value,
                    title=item.title,
                    content=item.content,
                    normalized_content=normalize_content(item.content),
                    line_start=item.line_start,
                    line_end=item.line_end,
                    status=item.status.value,
                    content_hash=sha256_text(item.content),
                )
            )

    def _is_unchanged(
        self,
        *,
        poem: Poem,
        record: CorpusImportRecord,
        source: PoemSource,
        dataset_source: CorpusImportSource,
        author: Author | None,
        dynasty: Dynasty | None,
        category_ids: list[int],
        current_annotations: list[PoemAnnotation],
        publish: bool,
    ) -> bool:
        if (
            poem.title != record.title
            or poem.content != record.content
            or poem.summary != record.summary
            or poem.author_id != (author.id if author is not None else None)
            or poem.dynasty_id != (dynasty.id if dynasty is not None else None)
        ):
            return False
        if sorted(link.category_id for link in poem.category_links) != sorted(category_ids):
            return False
        if sorted(link.tag.normalized_name for link in poem.tag_links) != sorted(
            normalize_lookup(tag) for tag in record.tags
        ):
            return False
        if publish and poem.status != PoemStatus.PUBLISHED.value:
            return False
        if not self._source_matches(source, record, dataset_source):
            return False
        return self._annotations_match(current_annotations, record.annotations)

    @staticmethod
    def _source_matches(
        source: PoemSource,
        record: CorpusImportRecord,
        dataset_source: CorpusImportSource,
    ) -> bool:
        return (
            source.source_type == dataset_source.source_type
            and source.source_key == dataset_source.source_key
            and source.source_name == dataset_source.source_name
            and source.source_url == (record.source_url or dataset_source.source_url)
            and source.raw_title == record.title
            and source.raw_author_name == record.author_name
            and source.raw_dynasty_name == record.dynasty_name
            and source.raw_content == record.content
            and source.raw_payload == record.raw_payload
            and source.content_hash == sha256_text(record.content)
            and source.license_note == dataset_source.license_note
        )

    @staticmethod
    def _annotations_match(
        current: list[PoemAnnotation],
        imported: list[CorpusImportAnnotation],
    ) -> bool:
        current_values = sorted(
            [
                (
                    item.annotation_type,
                    item.title,
                    item.content,
                    item.line_start,
                    item.line_end,
                    item.status,
                )
                for item in current
            ],
            key=_annotation_sort_key,
        )
        imported_values = sorted(
            [
                (
                    item.type.value,
                    item.title,
                    item.content,
                    item.line_start,
                    item.line_end,
                    item.status.value,
                )
                for item in imported
            ],
            key=_annotation_sort_key,
        )
        return current_values == imported_values

    @staticmethod
    def _publish_if_requested(poem: Poem, *, publish: bool) -> None:
        if not publish or poem.status == PoemStatus.PUBLISHED.value:
            return
        poem.status = PoemStatus.PUBLISHED.value
        poem.published_at = datetime.now(UTC)

    @staticmethod
    def _update_source_metadata(
        source: PoemSource,
        *,
        record: CorpusImportRecord,
        dataset_source: CorpusImportSource,
    ) -> None:
        source.source_type = dataset_source.source_type
        source.source_key = dataset_source.source_key
        source.source_name = dataset_source.source_name
        source.source_url = record.source_url or dataset_source.source_url
        source.raw_title = record.title
        source.raw_author_name = record.author_name
        source.raw_dynasty_name = record.dynasty_name
        source.raw_content = record.content
        source.raw_payload = record.raw_payload
        source.content_hash = sha256_text(record.content)
        source.license_note = dataset_source.license_note
        source.fetched_at = datetime.now(UTC)


def _annotation_sort_key(
    value: tuple[str, str | None, str, int | None, int | None, str],
) -> tuple[str, str, str, int, int, str]:
    annotation_type, title, content, line_start, line_end, status = value
    return (
        annotation_type,
        title or "",
        content,
        line_start or 0,
        line_end or 0,
        status,
    )
