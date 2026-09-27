from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.text import normalize_lookup
from app.models.domain_label import (
    DomainLabel,
    DomainLabelAlias,
    DomainLabelDimension,
    DomainLabelGenerationMethod,
    DomainLabelReviewStatus,
    DomainLabelStatus,
    PoemVersionDomainLabel,
)
from app.repositories.domain_labels import DomainLabelRepository
from app.schemas.common import PageResult
from app.schemas.domain_label import (
    DomainLabelAssignmentCreate,
    DomainLabelAssignmentRead,
    DomainLabelAssignmentReviewRequest,
    DomainLabelCreate,
    DomainLabelRead,
    DomainLabelReviewAction,
    DomainLabelUpdate,
    PoemDomainLabelRead,
)

_ASSIGNMENT_PRIORITY = {
    DomainLabelGenerationMethod.MANUAL.value: 0,
    DomainLabelGenerationMethod.PUBLIC_DATASET.value: 1,
    DomainLabelGenerationMethod.AI.value: 2,
}


class DomainLabelService:
    def __init__(self, session: AsyncSession, *, actor_id: int | None = None) -> None:
        self.session = session
        self.actor_id = actor_id
        self.repository = DomainLabelRepository(session)

    async def list_labels(
        self,
        *,
        page: int,
        page_size: int,
        dimension: str | None = None,
        status: str | None = None,
        q: str | None = None,
    ) -> PageResult[DomainLabelRead]:
        labels, total = await self.repository.list_labels(
            page=page,
            page_size=page_size,
            dimension=dimension,
            status=status,
            q=q,
        )
        return PageResult(
            items=[self._serialize_label(label) for label in labels],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def create_label(self, payload: DomainLabelCreate) -> DomainLabelRead:
        dimension = payload.dimension.value
        normalized_name = normalize_lookup(payload.canonical_name)
        existing = await self.repository.get_label_by_normalized_name(
            dimension=dimension,
            normalized_name=normalized_name,
        )
        if existing is not None:
            raise AppError(
                status_code=409,
                code=ErrorCode.DOMAIN_LABEL_EXISTS,
                message="同维度下已存在同名标签",
            )

        label = self.repository.create_label(
            dimension=dimension,
            canonical_name=payload.canonical_name,
            normalized_name=normalized_name,
            description=payload.description,
        )
        label.aliases = [
            DomainLabelAlias(alias=alias, normalized_alias=normalize_lookup(alias))
            for alias in payload.aliases
        ]
        await self.session.commit()
        return self._serialize_label(await self._get_label_or_raise(label.id))

    async def update_label(
        self,
        label_id: int,
        payload: DomainLabelUpdate,
    ) -> DomainLabelRead:
        label = await self._get_label_or_raise(label_id)
        data = payload.model_dump(exclude_unset=True)

        if "aliases" in data and payload.aliases is not None:
            self._replace_aliases(label, payload.aliases)

        if "canonical_name" in data and payload.canonical_name is not None:
            normalized_name = normalize_lookup(payload.canonical_name)
            existing = await self.repository.get_label_by_normalized_name(
                dimension=label.dimension,
                normalized_name=normalized_name,
            )
            if existing is not None and existing.id != label.id:
                raise AppError(
                    status_code=409,
                    code=ErrorCode.DOMAIN_LABEL_EXISTS,
                    message="同维度下已存在同名标签",
                )
            label.canonical_name = payload.canonical_name
            label.normalized_name = normalized_name

        if "description" in data:
            label.description = payload.description

        target_status = label.status
        if "status" in data and payload.status is not None:
            target_status = payload.status.value
        if "merged_into_id" in data and payload.merged_into_id is not None:
            if (
                "status" in data
                and payload.status is not None
                and payload.status != DomainLabelStatus.MERGED
            ):
                raise self._invalid_merge("merged_into_id 只能与 merged 状态一起使用")
            target_status = DomainLabelStatus.MERGED.value

        if target_status == DomainLabelStatus.MERGED.value:
            target_id = (
                payload.merged_into_id
                if "merged_into_id" in data and payload.merged_into_id is not None
                else label.merged_into_id
            )
            if target_id is None:
                raise self._invalid_merge("合并标签必须提供 merged_into_id")
            target = await self._validate_merge_target(label, target_id)
            label.status = DomainLabelStatus.MERGED.value
            label.merged_into_id = target.id
            self._copy_aliases_to_target(label, target)
        else:
            label.status = target_status
            label.merged_into_id = None

        await self.session.commit()
        return self._serialize_label(await self._get_label_or_raise(label.id))

    async def list_assignments(
        self,
        *,
        label_id: int,
        page: int,
        page_size: int,
        review_status: str | None = None,
        generation_method: str | None = None,
    ) -> PageResult[DomainLabelAssignmentRead]:
        await self._get_label_or_raise(label_id)
        assignments, total = await self.repository.list_assignments(
            label_id=label_id,
            page=page,
            page_size=page_size,
            review_status=review_status,
            generation_method=generation_method,
        )
        return PageResult(
            items=[self._serialize_assignment(item) for item in assignments],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def create_assignment(
        self,
        poem_id: int,
        payload: DomainLabelAssignmentCreate,
    ) -> tuple[DomainLabelAssignmentRead, bool]:
        version = await self.repository.get_current_version(poem_id)
        if version is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_VERSION_NOT_FOUND,
                message="诗词当前版本不存在",
            )

        label = await self._get_label_or_raise(payload.domain_label_id)
        if label.status != DomainLabelStatus.ACTIVE.value:
            raise AppError(
                status_code=409,
                code=ErrorCode.DOMAIN_LABEL_NOT_FOUND,
                message="标签已合并或废弃，不能新增关联",
            )

        origin_ref = payload.origin_ref or self._default_origin_ref(
            generation_method=payload.generation_method,
            label_id=label.id,
        )
        existing = await self.repository.get_assignment_by_origin(
            poem_version_id=version.id,
            label_id=label.id,
            origin_ref=origin_ref,
        )
        if existing is not None:
            return self._serialize_assignment(existing), False

        assignment = PoemVersionDomainLabel(
            poem_version_id=version.id,
            domain_label_id=label.id,
            generation_method=payload.generation_method.value,
            origin_ref=origin_ref,
            confidence=payload.confidence,
            review_status=DomainLabelReviewStatus.PENDING.value,
            evidence_text=payload.evidence_text,
            line_start=payload.line_start,
            line_end=payload.line_end,
            model_name=payload.model_name,
            task_version=payload.task_version,
            created_by_id=self.actor_id,
        )
        self.session.add(assignment)
        await self.session.commit()
        return self._serialize_assignment(await self._get_assignment_or_raise(assignment.id)), True

    async def review_assignment(
        self,
        assignment_id: int,
        payload: DomainLabelAssignmentReviewRequest,
    ) -> DomainLabelAssignmentRead:
        assignment = await self._get_assignment_or_raise(assignment_id)
        current_status = assignment.review_status
        now = datetime.now(UTC)

        if (
            payload.action == DomainLabelReviewAction.APPROVE
            and current_status == DomainLabelReviewStatus.PENDING.value
        ):
            assignment.review_status = DomainLabelReviewStatus.APPROVED.value
            assignment.archived_at = None
        elif (
            payload.action == DomainLabelReviewAction.REJECT
            and current_status == DomainLabelReviewStatus.PENDING.value
        ):
            assignment.review_status = DomainLabelReviewStatus.REJECTED.value
            assignment.archived_at = None
        elif (
            payload.action == DomainLabelReviewAction.ARCHIVE
            and current_status == DomainLabelReviewStatus.APPROVED.value
        ):
            assignment.review_status = DomainLabelReviewStatus.ARCHIVED.value
            assignment.archived_at = now
        elif (
            payload.action == DomainLabelReviewAction.REASSESS
            and current_status == DomainLabelReviewStatus.REJECTED.value
        ):
            assignment.review_status = DomainLabelReviewStatus.PENDING.value
            assignment.reviewed_by_id = None
            assignment.reviewed_at = None
            assignment.archived_at = None
        else:
            raise AppError(
                status_code=409,
                code=ErrorCode.DOMAIN_LABEL_INVALID_REVIEW_TRANSITION,
                message=f"不能从 {current_status} 执行 {payload.action.value} 操作",
            )

        if payload.action != DomainLabelReviewAction.REASSESS:
            assignment.reviewed_by_id = self.actor_id
            assignment.reviewed_at = now

        await self.session.commit()
        return self._serialize_assignment(await self._get_assignment_or_raise(assignment.id))

    async def list_public_poem_labels(self, poem_id: int) -> list[PoemDomainLabelRead]:
        version = await self.repository.get_current_version(poem_id, public_only=True)
        if version is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_NOT_FOUND,
                message="诗词不存在或尚未发布",
            )

        approved = await self.repository.list_approved_for_version(version.id)
        selected: dict[int, tuple[int, PoemVersionDomainLabel, DomainLabel]] = {}
        for assignment in approved:
            label = assignment.domain_label
            if label.status == DomainLabelStatus.MERGED.value:
                target = label.merged_into
                if target is None or target.status != DomainLabelStatus.ACTIVE.value:
                    continue
                label = target
            elif label.status != DomainLabelStatus.ACTIVE.value:
                continue

            priority = _ASSIGNMENT_PRIORITY[assignment.generation_method]
            current = selected.get(label.id)
            if current is None or priority < current[0]:
                selected[label.id] = (priority, assignment, label)

        items = [
            PoemDomainLabelRead(
                label_id=label.id,
                dimension=DomainLabelDimension(label.dimension),
                canonical_name=label.canonical_name,
                description=label.description,
                generation_method=DomainLabelGenerationMethod(
                    assignment.generation_method
                ),
                evidence_text=assignment.evidence_text,
                line_start=assignment.line_start,
                line_end=assignment.line_end,
            )
            for _, assignment, label in selected.values()
        ]
        items.sort(key=lambda item: (item.dimension.value, item.label_id))
        return items

    async def _get_label_or_raise(self, label_id: int) -> DomainLabel:
        label = await self.repository.get_label(label_id)
        if label is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.DOMAIN_LABEL_NOT_FOUND,
                message="领域标签不存在",
            )
        return label

    async def _get_assignment_or_raise(
        self,
        assignment_id: int,
    ) -> PoemVersionDomainLabel:
        assignment = await self.repository.get_assignment(assignment_id)
        if assignment is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.DOMAIN_LABEL_ASSIGNMENT_NOT_FOUND,
                message="领域标签关联不存在",
            )
        return assignment

    async def _validate_merge_target(
        self,
        label: DomainLabel,
        target_id: int,
    ) -> DomainLabel:
        if target_id == label.id:
            raise self._invalid_merge("标签不能合并到自身")
        target = await self.repository.get_label(target_id)
        if target is None:
            raise self._invalid_merge("目标标签不存在")
        if target.dimension != label.dimension:
            raise self._invalid_merge("只能合并同维度标签")
        if target.status != DomainLabelStatus.ACTIVE.value:
            raise self._invalid_merge("目标标签必须处于 active 状态")
        if target.merged_into_id is not None:
            raise self._invalid_merge("不允许形成多级合并链")
        return target

    @staticmethod
    def _replace_aliases(label: DomainLabel, aliases: list[str]) -> None:
        existing = {item.normalized_alias: item for item in label.aliases}
        selected: list[DomainLabelAlias] = []
        for alias in aliases:
            normalized_alias = normalize_lookup(alias)
            item = existing.get(normalized_alias)
            if item is None:
                item = DomainLabelAlias(
                    alias=alias,
                    normalized_alias=normalized_alias,
                )
            else:
                item.alias = alias
            selected.append(item)
        label.aliases = selected

    @staticmethod
    def _copy_aliases_to_target(label: DomainLabel, target: DomainLabel) -> None:
        target_aliases = {item.normalized_alias for item in target.aliases}
        alias_names = [label.canonical_name, *(item.alias for item in label.aliases)]
        for alias in alias_names:
            normalized_alias = normalize_lookup(alias)
            if not normalized_alias or normalized_alias in target_aliases:
                continue
            target.aliases.append(
                DomainLabelAlias(
                    alias=alias,
                    normalized_alias=normalized_alias,
                )
            )
            target_aliases.add(normalized_alias)

    def _default_origin_ref(
        self,
        *,
        generation_method: DomainLabelGenerationMethod,
        label_id: int,
    ) -> str:
        actor = "system" if self.actor_id is None else str(self.actor_id)
        return f"{generation_method.value}:{actor}:{label_id}"

    @staticmethod
    def _invalid_merge(message: str) -> AppError:
        return AppError(
            status_code=409,
            code=ErrorCode.DOMAIN_LABEL_INVALID_MERGE,
            message=message,
        )

    @staticmethod
    def _serialize_label(label: DomainLabel) -> DomainLabelRead:
        return DomainLabelRead(
            id=label.id,
            dimension=DomainLabelDimension(label.dimension),
            canonical_name=label.canonical_name,
            normalized_name=label.normalized_name,
            description=label.description,
            status=DomainLabelStatus(label.status),
            merged_into_id=label.merged_into_id,
            aliases=sorted(label.aliases, key=lambda item: item.id or 0),
            created_at=label.created_at,
            updated_at=label.updated_at,
        )

    @staticmethod
    def _serialize_assignment(
        assignment: PoemVersionDomainLabel,
    ) -> DomainLabelAssignmentRead:
        return DomainLabelAssignmentRead(
            id=assignment.id,
            poem_id=assignment.version.poem_id,
            poem_version_id=assignment.poem_version_id,
            version_no=assignment.version.version_no,
            label=DomainLabelService._serialize_label(assignment.domain_label),
            generation_method=DomainLabelGenerationMethod(
                assignment.generation_method
            ),
            origin_ref=assignment.origin_ref,
            confidence=(
                float(assignment.confidence) if assignment.confidence is not None else None
            ),
            review_status=DomainLabelReviewStatus(assignment.review_status),
            evidence_text=assignment.evidence_text,
            line_start=assignment.line_start,
            line_end=assignment.line_end,
            model_name=assignment.model_name,
            task_version=assignment.task_version,
            created_by_id=assignment.created_by_id,
            reviewed_by_id=assignment.reviewed_by_id,
            reviewed_at=assignment.reviewed_at,
            archived_at=assignment.archived_at,
            created_at=assignment.created_at,
            updated_at=assignment.updated_at,
        )
