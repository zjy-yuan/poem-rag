from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize_lookup
from app.models.domain_label import (
    DomainLabel,
    DomainLabelReviewStatus,
    PoemVersionDomainLabel,
)
from app.models.poem import Poem
from app.models.source import PoemSource
from app.models.version import PoemVersion
from app.repositories.domain_labels import DomainLabelRepository
from app.schemas.domain_label_import import (
    MAX_ORIGIN_REF_LENGTH,
    DomainLabelImportDataset,
    DomainLabelImportLabel,
    DomainLabelImportRecord,
    DomainLabelImportRecordResult,
    DomainLabelImportReport,
    build_domain_label_origin_ref,
)


@dataclass(slots=True)
class _ResolvedLabel:
    input_label: DomainLabelImportLabel
    label: DomainLabel
    origin_ref: str


@dataclass(slots=True)
class _ResolvedRecord:
    source: PoemSource
    poem: Poem
    version: PoemVersion
    labels: list[_ResolvedLabel]


class DomainLabelImportService:
    """Import version-scoped domain labels without bypassing review governance."""

    def __init__(self, session: AsyncSession, *, actor_id: int | None = None) -> None:
        self.session = session
        self.actor_id = actor_id
        self.repository = DomainLabelRepository(session)

    async def import_dataset(
        self,
        dataset: DomainLabelImportDataset,
        *,
        dry_run: bool = False,
    ) -> DomainLabelImportReport:
        results: list[DomainLabelImportRecordResult] = []
        for record in dataset.records:
            try:
                if dry_run:
                    result = await self._process_record(dataset, record, dry_run=True)
                else:
                    async with self.session.begin_nested():
                        result = await self._process_record(dataset, record, dry_run=False)
                    await self.session.commit()
            except Exception as exc:
                await self.session.rollback()
                result = DomainLabelImportRecordResult(
                    external_id=record.external_id,
                    status="failed",
                    label_count=len(record.labels),
                    created_labels=0,
                    unchanged_labels=0,
                    failed_labels=len(record.labels),
                    message=str(exc),
                )
            results.append(result)

        return DomainLabelImportReport(
            dry_run=dry_run,
            dataset_version=dataset.version,
            source_key=dataset.source.source_key,
            poem_source_key=dataset.poem_source_key,
            generation_method=dataset.generation_method,
            total_records=len(results),
            created_records=sum(item.status == "created" for item in results),
            unchanged_records=sum(item.status == "unchanged" for item in results),
            failed_records=sum(item.status == "failed" for item in results),
            created_assignments=sum(item.created_labels for item in results),
            unchanged_assignments=sum(item.unchanged_labels for item in results),
            failed_assignments=sum(item.failed_labels for item in results),
            records=results,
        )

    async def _process_record(
        self,
        dataset: DomainLabelImportDataset,
        record: DomainLabelImportRecord,
        *,
        dry_run: bool,
    ) -> DomainLabelImportRecordResult:
        resolved = await self._resolve_record(dataset, record)
        created_labels = 0
        unchanged_labels = 0

        for item in resolved.labels:
            existing = await self.repository.get_assignment_by_origin(
                poem_version_id=resolved.version.id,
                label_id=item.label.id,
                origin_ref=item.origin_ref,
            )
            if existing is not None:
                unchanged_labels += 1
                continue

            created_labels += 1
            if dry_run:
                continue

            self.session.add(
                PoemVersionDomainLabel(
                    poem_version_id=resolved.version.id,
                    domain_label_id=item.label.id,
                    source_id=resolved.source.id,
                    generation_method=dataset.generation_method.value,
                    origin_ref=item.origin_ref,
                    confidence=item.input_label.confidence,
                    review_status=DomainLabelReviewStatus.PENDING.value,
                    evidence_text=item.input_label.evidence_text,
                    line_start=item.input_label.line_start,
                    line_end=item.input_label.line_end,
                    model_name=dataset.model_name,
                    task_version=dataset.task_version,
                    created_by_id=self.actor_id,
                )
            )

        status = "created" if created_labels else "unchanged"
        return DomainLabelImportRecordResult(
            external_id=record.external_id,
            status=status,
            poem_id=resolved.poem.id,
            version_id=resolved.version.id,
            version_no=resolved.version.version_no,
            label_count=len(record.labels),
            created_labels=created_labels,
            unchanged_labels=unchanged_labels,
            failed_labels=0,
        )

    async def _resolve_record(
        self,
        dataset: DomainLabelImportDataset,
        record: DomainLabelImportRecord,
    ) -> _ResolvedRecord:
        source = await self.session.scalar(
            select(PoemSource).where(
                PoemSource.source_key == dataset.poem_source_key,
                PoemSource.external_id == record.external_id,
            )
        )
        if source is None:
            raise ValueError(
                "未找到对应作品来源: "
                f"source_key={dataset.poem_source_key} external_id={record.external_id}"
            )

        poem = await self.session.get(Poem, source.poem_id)
        if poem is None:
            raise ValueError(f"来源关联的诗词不存在: poem_id={source.poem_id}")

        version = await self.repository.get_current_version(poem.id)
        if version is None:
            raise ValueError(f"诗词当前版本不存在: poem_id={poem.id}")

        labels: list[_ResolvedLabel] = []
        seen: set[tuple[int, str]] = set()
        for input_label in record.labels:
            normalized_name = normalize_lookup(input_label.name)
            candidates = await self.repository.list_active_labels_by_name(
                dimension=input_label.dimension.value,
                normalized_name=normalized_name,
            )
            if not candidates:
                raise ValueError(
                    "标签不存在或未启用: "
                    f"dimension={input_label.dimension.value} name={input_label.name}"
                )
            if len(candidates) > 1:
                candidate_names = ", ".join(item.canonical_name for item in candidates)
                raise ValueError(
                    "标签名称在当前维度存在歧义: "
                    f"name={input_label.name} candidates={candidate_names}"
                )

            label = candidates[0]
            origin_ref = build_domain_label_origin_ref(
                generation_method=dataset.generation_method,
                source_key=dataset.source.source_key,
                dataset_version=dataset.version,
                external_id=record.external_id,
                dimension=input_label.dimension,
                label_name=label.normalized_name,
            )
            if len(origin_ref) > MAX_ORIGIN_REF_LENGTH:
                raise ValueError(
                    "origin_ref 超过数据库长度限制: "
                    f"external_id={record.external_id} label={label.canonical_name}"
                )

            key = (label.id, origin_ref)
            if key in seen:
                continue
            seen.add(key)
            labels.append(
                _ResolvedLabel(
                    input_label=input_label,
                    label=label,
                    origin_ref=origin_ref,
                )
            )

        return _ResolvedRecord(
            source=source,
            poem=poem,
            version=version,
            labels=labels,
        )
