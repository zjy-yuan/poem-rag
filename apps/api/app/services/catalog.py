from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.text import normalize_content, normalize_lookup, sha256_text
from app.models.author import Author
from app.models.category import Category
from app.models.dynasty import Dynasty
from app.models.poem import Poem, PoemCategory, PoemStatus
from app.models.source import PoemSource
from app.models.tag import PoemTag, Tag
from app.models.version import PoemVersion, PoemVersionChangeType
from app.repositories.authors import AuthorRepository
from app.repositories.categories import CategoryRepository
from app.repositories.dynasties import DynastyRepository
from app.repositories.poems import PoemRepository
from app.schemas.catalog import (
    AuthorCreate,
    AuthorDetail,
    AuthorRead,
    AuthorUpdate,
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
    DynastyCreate,
    DynastyRead,
    DynastyUpdate,
    PoemCreate,
    PoemRead,
    PoemSummary,
    PoemUpdate,
    TagRead,
)
from app.schemas.common import PageResult


class CatalogService:
    def __init__(self, session: AsyncSession, *, changed_by_id: int | None = None) -> None:
        self.session = session
        self.changed_by_id = changed_by_id
        self.poems = PoemRepository(session)
        self.authors = AuthorRepository(session)
        self.dynasties = DynastyRepository(session)
        self.categories = CategoryRepository(session)

    async def list_poems(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
        category_id: int | None = None,
    ) -> PageResult[PoemRead]:
        poems, total = await self.poems.list_public(
            page=page,
            page_size=page_size,
            q=q,
            author_id=author_id,
            dynasty_id=dynasty_id,
            category_id=category_id,
        )
        return PageResult(
            items=[self._serialize_poem(poem) for poem in poems],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_poem(self, poem_id: int) -> PoemRead:
        poem = await self.poems.get_public(poem_id)
        if poem is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_NOT_FOUND,
                message="诗词不存在或尚未发布",
            )
        return self._serialize_poem(poem)

    async def list_admin_poems(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        include_deleted: bool = False,
        q: str | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
        category_id: int | None = None,
    ) -> PageResult[PoemRead]:
        if status is not None:
            self._validate_status(status)
        poems, total = await self.poems.list_admin(
            page=page,
            page_size=page_size,
            status=status,
            include_deleted=include_deleted,
            q=q,
            author_id=author_id,
            dynasty_id=dynasty_id,
            category_id=category_id,
        )
        return PageResult(
            items=[self._serialize_poem(poem) for poem in poems],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_admin_poem(self, poem_id: int) -> PoemRead:
        poem = await self.poems.get_admin(poem_id)
        if poem is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_NOT_FOUND,
                message="诗词不存在",
            )
        return self._serialize_poem(poem)

    async def create_poem(self, payload: PoemCreate) -> PoemRead:
        await self._validate_poem_references(
            author_id=payload.author_id,
            dynasty_id=payload.dynasty_id,
            category_ids=payload.category_ids,
        )
        poem = self.poems.create(
            title=payload.title,
            content=payload.content,
            normalized_content=normalize_content(payload.content),
            summary=payload.summary,
            author_id=payload.author_id,
            dynasty_id=payload.dynasty_id,
        )
        await self.replace_categories(poem, payload.category_ids)
        await self.replace_tags(poem, payload.tag_names)
        await self.session.flush()
        source = self._create_manual_source(poem)
        self.session.add(source)
        await self.session.flush()
        await self.record_version(
            poem,
            change_type=PoemVersionChangeType.CREATE,
            source_id=source.id,
        )
        await self.session.commit()
        return await self.get_admin_poem(poem.id)

    async def update_poem(self, poem_id: int, payload: PoemUpdate) -> PoemRead:
        poem = await self.poems.get_admin(poem_id)
        if poem is None or poem.deleted_at is not None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_NOT_FOUND,
                message="诗词不存在",
            )
        if payload.version_no != poem.version_no:
            raise AppError(
                status_code=409,
                code=ErrorCode.POEM_VERSION_CONFLICT,
                message="诗词已被其他操作修改，请刷新后重试",
            )

        await self._validate_poem_references(
            author_id=payload.author_id if "author_id" in payload.model_fields_set else None,
            dynasty_id=payload.dynasty_id if "dynasty_id" in payload.model_fields_set else None,
            category_ids=(
                payload.category_ids
                if "category_ids" in payload.model_fields_set and payload.category_ids is not None
                else []
            ),
        )

        data = payload.model_dump(exclude_unset=True)
        for field in ("title", "author_id", "dynasty_id", "summary"):
            if field in data:
                setattr(poem, field, data[field])
        if "content" in data and data["content"] is not None:
            poem.content = data["content"]
            poem.normalized_content = normalize_content(data["content"])
        if payload.category_ids is not None and "category_ids" in payload.model_fields_set:
            await self.replace_categories(poem, payload.category_ids)
        if payload.tag_names is not None and "tag_names" in payload.model_fields_set:
            await self.replace_tags(poem, payload.tag_names)

        poem.version_no += 1
        await self.session.flush()
        await self.record_version(
            poem,
            change_type=PoemVersionChangeType.UPDATE,
            source_id=await self._get_latest_source_id(poem.id),
        )
        await self.session.commit()
        return await self.get_admin_poem(poem_id)

    async def publish_poem(self, poem_id: int) -> PoemRead:
        poem = await self._get_mutable_poem(poem_id)
        if poem.status == PoemStatus.PUBLISHED.value:
            raise AppError(
                status_code=409,
                code=ErrorCode.POEM_INVALID_STATUS,
                message="诗词已经发布",
            )
        poem.status = PoemStatus.PUBLISHED.value
        poem.published_at = datetime.now(UTC)
        await self.session.commit()
        return await self.get_admin_poem(poem_id)

    async def unpublish_poem(self, poem_id: int) -> PoemRead:
        poem = await self._get_mutable_poem(poem_id)
        if poem.status != PoemStatus.PUBLISHED.value:
            raise AppError(
                status_code=409,
                code=ErrorCode.POEM_INVALID_STATUS,
                message="只有已发布诗词可以取消发布",
            )
        poem.status = PoemStatus.DRAFT.value
        poem.published_at = None
        await self.session.commit()
        return await self.get_admin_poem(poem_id)

    async def delete_poem(self, poem_id: int) -> None:
        poem = await self.poems.get_admin(poem_id)
        if poem is None or poem.deleted_at is not None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_NOT_FOUND,
                message="诗词不存在",
            )
        poem.status = PoemStatus.ARCHIVED.value
        poem.deleted_at = datetime.now(UTC)
        poem.version_no += 1
        await self.session.flush()
        await self.record_version(
            poem,
            change_type=PoemVersionChangeType.ARCHIVE,
            source_id=await self._get_latest_source_id(poem.id),
        )
        await self.session.commit()

    async def restore_poem(self, poem_id: int) -> PoemRead:
        poem = await self.poems.get_admin(poem_id)
        if poem is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_NOT_FOUND,
                message="诗词不存在",
            )
        if poem.deleted_at is None:
            raise AppError(
                status_code=409,
                code=ErrorCode.POEM_INVALID_STATUS,
                message="诗词未被删除",
            )
        poem.deleted_at = None
        poem.status = PoemStatus.DRAFT.value
        poem.published_at = None
        poem.version_no += 1
        await self.session.flush()
        await self.record_version(
            poem,
            change_type=PoemVersionChangeType.RESTORE,
            source_id=await self._get_latest_source_id(poem.id),
        )
        await self.session.commit()
        return await self.get_admin_poem(poem_id)

    async def list_authors(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        dynasty_id: int | None = None,
    ) -> PageResult[AuthorRead]:
        authors, total = await self.authors.list(
            page=page,
            page_size=page_size,
            q=q,
            dynasty_id=dynasty_id,
            public_only=True,
        )
        return PageResult(
            items=[self._serialize_author(author, count) for author, count in authors],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_author(self, author_id: int) -> AuthorDetail:
        author = await self.authors.get(author_id)
        if author is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.AUTHOR_NOT_FOUND,
                message="作者不存在",
            )
        poems = await self.poems.list_by_author_public(author_id)
        base = self._serialize_author(author, len(poems)).model_dump()
        return AuthorDetail(
            **base,
            poems=[
                PoemSummary(
                    id=poem.id,
                    title=poem.title,
                    summary=poem.summary,
                    status=PoemStatus(poem.status),
                    published_at=poem.published_at,
                )
                for poem in poems
            ],
        )

    async def list_admin_authors(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        dynasty_id: int | None = None,
    ) -> PageResult[AuthorRead]:
        authors, total = await self.authors.list(
            page=page,
            page_size=page_size,
            q=q,
            dynasty_id=dynasty_id,
            public_only=False,
        )
        return PageResult(
            items=[self._serialize_author(author, count) for author, count in authors],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_admin_author(self, author_id: int) -> AuthorRead:
        author = await self.authors.get(author_id, include_deleted=True)
        if author is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.AUTHOR_NOT_FOUND,
                message="作者不存在",
            )
        count = await self.authors.poem_count(author.id, public_only=False)
        return self._serialize_author(author, count)

    async def create_author(self, payload: AuthorCreate) -> AuthorRead:
        await self._validate_dynasty(payload.dynasty_id)
        normalized = normalize_lookup(payload.name)
        if await self.authors.get_by_normalized_name(normalized) is not None:
            raise AppError(
                status_code=409,
                code=ErrorCode.AUTHOR_EXISTS,
                message="作者已存在",
            )
        author = self.authors.create(name=payload.name, normalized_name=normalized)
        author.aliases = payload.aliases
        author.bio = payload.bio
        author.dynasty_id = payload.dynasty_id
        await self.session.commit()
        return await self.get_admin_author(author.id)

    async def update_author(self, author_id: int, payload: AuthorUpdate) -> AuthorRead:
        author = await self.authors.get(author_id, include_deleted=True)
        if author is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.AUTHOR_NOT_FOUND,
                message="作者不存在",
            )
        data = payload.model_dump(exclude_unset=True)
        if "dynasty_id" in data:
            await self._validate_dynasty(payload.dynasty_id)
        if "name" in data and payload.name is not None:
            normalized = normalize_lookup(payload.name)
            existing = await self.authors.get_by_normalized_name(normalized)
            if existing is not None and existing.id != author.id:
                raise AppError(
                    status_code=409,
                    code=ErrorCode.AUTHOR_EXISTS,
                    message="作者已存在",
                )
            author.name = payload.name
            author.normalized_name = normalized
        for field in ("aliases", "bio", "dynasty_id"):
            if field in data:
                setattr(author, field, data[field])
        await self.session.commit()
        return await self.get_admin_author(author_id)

    async def delete_author(self, author_id: int) -> None:
        author = await self.authors.get(author_id)
        if author is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.AUTHOR_NOT_FOUND,
                message="作者不存在",
            )
        await self.authors.delete(author)
        await self.session.commit()

    async def list_dynasties(self) -> list[DynastyRead]:
        return [DynastyRead.model_validate(item) for item in await self.dynasties.list()]

    async def create_dynasty(self, payload: DynastyCreate) -> DynastyRead:
        normalized = normalize_lookup(payload.name)
        if await self.dynasties.get_by_normalized_name(normalized) is not None:
            raise AppError(
                status_code=409,
                code=ErrorCode.DYNASTY_EXISTS,
                message="朝代已存在",
            )
        dynasty = self.dynasties.create(name=payload.name, normalized_name=normalized)
        dynasty.description = payload.description
        dynasty.sort_order = payload.sort_order
        await self.session.commit()
        await self.session.refresh(dynasty)
        return DynastyRead.model_validate(dynasty)

    async def update_dynasty(self, dynasty_id: int, payload: DynastyUpdate) -> DynastyRead:
        dynasty = await self.dynasties.get(dynasty_id)
        if dynasty is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.DYNASTY_NOT_FOUND,
                message="朝代不存在",
            )
        data = payload.model_dump(exclude_unset=True)
        if "name" in data and payload.name is not None:
            normalized = normalize_lookup(payload.name)
            existing = await self.dynasties.get_by_normalized_name(normalized)
            if existing is not None and existing.id != dynasty.id:
                raise AppError(
                    status_code=409,
                    code=ErrorCode.DYNASTY_EXISTS,
                    message="朝代已存在",
                )
            dynasty.name = payload.name
            dynasty.normalized_name = normalized
        for field in ("description", "sort_order"):
            if field in data:
                setattr(dynasty, field, data[field])
        await self.session.commit()
        await self.session.refresh(dynasty)
        return DynastyRead.model_validate(dynasty)

    async def delete_dynasty(self, dynasty_id: int) -> None:
        dynasty = await self.dynasties.get(dynasty_id)
        if dynasty is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.DYNASTY_NOT_FOUND,
                message="朝代不存在",
            )
        if await self.dynasties.poem_count(dynasty_id) > 0:
            raise AppError(
                status_code=409,
                code=ErrorCode.DYNASTY_IN_USE,
                message="该朝代仍有诗词，不能删除",
            )
        await self.dynasties.delete(dynasty)
        await self.session.commit()

    async def list_categories(
        self,
        *,
        category_type: str | None = None,
        include_inactive: bool = False,
        public_only: bool = True,
    ) -> list[CategoryRead]:
        categories = await self.categories.list(
            category_type=category_type,
            include_inactive=include_inactive,
            public_only=public_only,
        )
        return [self._serialize_category(item, count) for item, count in categories]

    async def create_category(self, payload: CategoryCreate) -> CategoryRead:
        await self._validate_category_parent(payload.parent_id)
        normalized = normalize_lookup(payload.name)
        if await self.categories.get_by_normalized_name(normalized, payload.type.value) is not None:
            raise AppError(
                status_code=409,
                code=ErrorCode.CATEGORY_EXISTS,
                message="同类型分类已存在",
            )
        category = self.categories.create(
            name=payload.name,
            normalized_name=normalized,
            category_type=payload.type.value,
        )
        category.parent_id = payload.parent_id
        category.sort_order = payload.sort_order
        category.is_active = payload.is_active
        await self.session.commit()
        await self.session.refresh(category)
        return self._serialize_category(category, 0)

    async def update_category(
        self,
        category_id: int,
        payload: CategoryUpdate,
    ) -> CategoryRead:
        category = await self.categories.get(category_id)
        if category is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.CATEGORY_NOT_FOUND,
                message="分类不存在",
            )
        data = payload.model_dump(exclude_unset=True)
        target_name = payload.name or category.name
        target_type = payload.type.value if payload.type is not None else category.type
        normalized = normalize_lookup(target_name)
        existing = await self.categories.get_by_normalized_name(normalized, target_type)
        if existing is not None and existing.id != category.id:
            raise AppError(
                status_code=409,
                code=ErrorCode.CATEGORY_EXISTS,
                message="同类型分类已存在",
            )
        if "parent_id" in data:
            await self._validate_category_parent(payload.parent_id, category_id=category.id)
        if "name" in data and payload.name is not None:
            category.name = payload.name
            category.normalized_name = normalized
        for field in ("type", "parent_id", "sort_order", "is_active"):
            if field in data:
                value = data[field]
                setattr(category, field, value.value if hasattr(value, "value") else value)
        await self.session.commit()
        await self.session.refresh(category)
        return self._serialize_category(category, 0)

    async def disable_category(self, category_id: int) -> None:
        category = await self.categories.get(category_id)
        if category is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.CATEGORY_NOT_FOUND,
                message="分类不存在",
            )
        if await self.categories.has_children(category_id):
            raise AppError(
                status_code=409,
                code=ErrorCode.CATEGORY_HAS_CHILDREN,
                message="分类仍有子分类，不能停用",
            )
        category.is_active = False
        await self.session.commit()

    async def _get_mutable_poem(self, poem_id: int) -> Poem:
        poem = await self.poems.get_admin(poem_id)
        if poem is None or poem.deleted_at is not None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_NOT_FOUND,
                message="诗词不存在",
            )
        return poem

    async def _validate_poem_references(
        self,
        *,
        author_id: int | None,
        dynasty_id: int | None,
        category_ids: list[int],
    ) -> None:
        if author_id is not None:
            author = await self.authors.get(author_id)
            if author is None:
                raise AppError(
                    status_code=404,
                    code=ErrorCode.AUTHOR_NOT_FOUND,
                    message="作者不存在",
                )
        await self._validate_dynasty(dynasty_id)
        if category_ids:
            result = await self.session.execute(
                select(Category.id).where(Category.id.in_(category_ids))
            )
            found = set(result.scalars())
            if found != set(category_ids):
                raise AppError(
                    status_code=404,
                    code=ErrorCode.CATEGORY_NOT_FOUND,
                    message="一个或多个分类不存在",
                )

    async def _validate_dynasty(self, dynasty_id: int | None) -> None:
        if dynasty_id is not None and await self.dynasties.get(dynasty_id) is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.DYNASTY_NOT_FOUND,
                message="朝代不存在",
            )

    async def _validate_category_parent(
        self,
        parent_id: int | None,
        *,
        category_id: int | None = None,
    ) -> None:
        if parent_id is None:
            return
        if category_id is not None and parent_id == category_id:
            raise AppError(
                status_code=409,
                code=ErrorCode.CATEGORY_HAS_CHILDREN,
                message="分类不能作为自己的父分类",
            )
        parent = await self.categories.get(parent_id)
        if parent is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.CATEGORY_NOT_FOUND,
                message="父分类不存在",
            )
        cursor: Category | None = parent
        for _ in range(32):
            if cursor is None:
                return
            if category_id is not None and cursor.id == category_id:
                raise AppError(
                    status_code=409,
                    code=ErrorCode.CATEGORY_HAS_CHILDREN,
                    message="分类层级不能形成循环",
                )
            cursor = await self.categories.get(cursor.parent_id) if cursor.parent_id else None
        raise AppError(
            status_code=409,
            code=ErrorCode.CATEGORY_HAS_CHILDREN,
            message="分类层级过深或存在循环",
        )

    async def replace_categories(
        self,
        poem: Poem,
        category_ids: list[int],
        *,
        source: str = "manual",
    ) -> None:
        poem.category_links.clear()
        for index, category_id in enumerate(category_ids):
            poem.category_links.append(
                PoemCategory(
                    category_id=category_id,
                    source=source,
                    is_primary=index == 0,
                )
            )

    async def replace_tags(self, poem: Poem, tag_names: list[str]) -> None:
        poem.tag_links.clear()
        if not tag_names:
            return
        normalized_items = [(name, normalize_lookup(name)) for name in tag_names]
        existing = await self.poems.get_tags([normalized for _, normalized in normalized_items])
        for index, (name, normalized) in enumerate(normalized_items):
            tag = existing.get(normalized)
            if tag is None:
                tag = Tag(name=name, normalized_name=normalized)
                self.session.add(tag)
                existing[normalized] = tag
            poem.tag_links.append(PoemTag(tag=tag, is_primary=index == 0))

    def _create_manual_source(self, poem: Poem) -> PoemSource:
        return self.create_source(
            poem,
            source_type="manual",
            source_key="manual",
            source_name="人工录入",
            raw_title=poem.title,
            raw_content=poem.content,
        )

    def create_source(
        self,
        poem: Poem,
        *,
        source_type: str,
        source_key: str,
        source_name: str | None,
        external_id: str | None = None,
        source_url: str | None = None,
        raw_title: str | None = None,
        raw_author_name: str | None = None,
        raw_dynasty_name: str | None = None,
        raw_content: str | None = None,
        raw_payload: dict[str, Any] | None = None,
        content_hash: str | None = None,
        license_note: str | None = None,
        fetched_at: datetime | None = None,
    ) -> PoemSource:
        return PoemSource(
            poem_id=poem.id,
            source_type=source_type,
            source_key=source_key,
            source_name=source_name,
            external_id=external_id,
            source_url=source_url,
            raw_title=raw_title,
            raw_author_name=raw_author_name,
            raw_dynasty_name=raw_dynasty_name,
            raw_content=raw_content,
            raw_payload=raw_payload,
            content_hash=content_hash or sha256_text(raw_content or poem.content),
            license_note=license_note,
            fetched_at=fetched_at or datetime.now(UTC),
        )

    async def record_version(
        self,
        poem: Poem,
        *,
        change_type: PoemVersionChangeType,
        source_id: int | None = None,
    ) -> PoemVersion:
        snapshot = await self._build_poem_snapshot(poem)
        canonical_snapshot = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        version = PoemVersion(
            poem_id=poem.id,
            source_id=source_id,
            version_no=poem.version_no,
            snapshot=snapshot,
            content_hash=sha256_text(canonical_snapshot),
            change_type=change_type.value,
            changed_by_id=self.changed_by_id,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def _get_latest_source_id(self, poem_id: int) -> int | None:
        return await self.session.scalar(
            select(PoemSource.id)
            .where(PoemSource.poem_id == poem_id)
            .order_by(PoemSource.id.desc())
            .limit(1)
        )

    async def _build_poem_snapshot(self, poem: Poem) -> dict[str, Any]:
        author_name = (
            await self.session.scalar(select(Author.name).where(Author.id == poem.author_id))
            if poem.author_id is not None
            else None
        )
        dynasty_name = (
            await self.session.scalar(select(Dynasty.name).where(Dynasty.id == poem.dynasty_id))
            if poem.dynasty_id is not None
            else None
        )

        category_rows = await self.session.execute(
            select(PoemCategory.category_id, Category.name)
            .join(Category, Category.id == PoemCategory.category_id)
            .where(PoemCategory.poem_id == poem.id)
            .order_by(Category.sort_order, Category.id)
        )
        categories = [
            {"id": category_id, "name": category_name}
            for category_id, category_name in category_rows.all()
        ]

        tag_rows = await self.session.execute(
            select(Tag.name)
            .join(PoemTag, PoemTag.tag_id == Tag.id)
            .where(PoemTag.poem_id == poem.id)
            .order_by(Tag.normalized_name)
        )
        tags = list(tag_rows.scalars())
        return {
            "title": poem.title,
            "author": {"id": poem.author_id, "name": author_name},
            "dynasty": {"id": poem.dynasty_id, "name": dynasty_name},
            "content": poem.content,
            "normalized_content": poem.normalized_content,
            "summary": poem.summary,
            "status": poem.status,
            "version_no": poem.version_no,
            "deleted_at": poem.deleted_at.isoformat() if poem.deleted_at is not None else None,
            "categories": categories,
            "tags": tags,
        }

    @staticmethod
    def _validate_status(status: str) -> None:
        try:
            PoemStatus(status)
        except ValueError as exc:
            raise AppError(
                status_code=422,
                code=ErrorCode.POEM_INVALID_STATUS,
                message="诗词状态不合法",
            ) from exc

    @staticmethod
    def _serialize_poem(poem: Poem) -> PoemRead:
        categories = [
            CategoryRead(
                id=link.category.id,
                name=link.category.name,
                type=link.category.type,
                parent_id=link.category.parent_id,
                sort_order=link.category.sort_order,
                is_active=link.category.is_active,
                poem_count=0,
                created_at=link.category.created_at,
                updated_at=link.category.updated_at,
            )
            for link in poem.category_links
            if link.category is not None
        ]
        categories.sort(key=lambda item: (not item.is_active, item.sort_order, item.id))
        return PoemRead(
            id=poem.id,
            title=poem.title,
            author_id=poem.author_id,
            author_name=poem.author.name if poem.author is not None else None,
            dynasty_id=poem.dynasty_id,
            dynasty_name=poem.dynasty.name if poem.dynasty is not None else None,
            content=poem.content,
            summary=poem.summary,
            status=PoemStatus(poem.status),
            version_no=poem.version_no,
            published_at=poem.published_at,
            deleted_at=poem.deleted_at,
            categories=categories,
            tags=[TagRead(id=link.tag.id, name=link.tag.name) for link in poem.tag_links],
            created_at=poem.created_at,
            updated_at=poem.updated_at,
        )

    @staticmethod
    def _serialize_author(author: Author, poem_count: int) -> AuthorRead:
        return AuthorRead(
            id=author.id,
            name=author.name,
            aliases=author.aliases,
            bio=author.bio,
            dynasty_id=author.dynasty_id,
            dynasty_name=author.dynasty.name if author.dynasty is not None else None,
            poem_count=poem_count,
            created_at=author.created_at,
            updated_at=author.updated_at,
        )

    @staticmethod
    def _serialize_category(category: Category, poem_count: int) -> CategoryRead:
        return CategoryRead(
            id=category.id,
            name=category.name,
            type=category.type,
            parent_id=category.parent_id,
            sort_order=category.sort_order,
            is_active=category.is_active,
            poem_count=poem_count,
            created_at=category.created_at,
            updated_at=category.updated_at,
        )
