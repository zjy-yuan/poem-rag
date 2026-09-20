"""Create RAG corpus tables and backfill existing poem versions.

Revision ID: 20260919_0003
Revises: 20260919_0002
Create Date: 2026-09-19
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_0003"
down_revision: str | None = "20260919_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def _canonical_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _backfill_poem_versions() -> None:
    bind = op.get_bind()

    poems = sa.table(
        "poems",
        sa.column("id", sa.Integer()),
        sa.column("title", sa.String()),
        sa.column("author_id", sa.Integer()),
        sa.column("dynasty_id", sa.Integer()),
        sa.column("content", sa.Text()),
        sa.column("normalized_content", sa.Text()),
        sa.column("summary", sa.Text()),
        sa.column("status", sa.String()),
        sa.column("version_no", sa.Integer()),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    authors = sa.table(
        "authors",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
    )
    dynasties = sa.table(
        "dynasties",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
    )
    categories = sa.table(
        "categories",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("sort_order", sa.Integer()),
    )
    poem_categories = sa.table(
        "poem_categories",
        sa.column("poem_id", sa.Integer()),
        sa.column("category_id", sa.Integer()),
    )
    tags = sa.table(
        "tags",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
    )
    poem_tags = sa.table(
        "poem_tags",
        sa.column("poem_id", sa.Integer()),
        sa.column("tag_id", sa.Integer()),
    )
    poem_versions = sa.table(
        "poem_versions",
        sa.column("poem_id", sa.Integer()),
        sa.column("source_id", sa.Integer()),
        sa.column("version_no", sa.Integer()),
        sa.column("snapshot", sa.JSON()),
        sa.column("content_hash", sa.String()),
        sa.column("change_type", sa.String()),
        sa.column("changed_by_id", sa.Integer()),
        sa.column("change_note", sa.String()),
    )

    author_names = dict(bind.execute(sa.select(authors.c.id, authors.c.name)).all())
    dynasty_names = dict(bind.execute(sa.select(dynasties.c.id, dynasties.c.name)).all())

    categories_by_poem: dict[int, list[dict[str, Any]]] = {}
    category_rows = bind.execute(
        sa.select(
            poem_categories.c.poem_id,
            categories.c.id,
            categories.c.name,
        )
        .select_from(
            poem_categories.join(
                categories,
                categories.c.id == poem_categories.c.category_id,
            )
        )
        .order_by(poem_categories.c.poem_id, categories.c.sort_order, categories.c.id)
    ).all()
    for poem_id, category_id, category_name in category_rows:
        categories_by_poem.setdefault(poem_id, []).append(
            {"id": category_id, "name": category_name}
        )

    tags_by_poem: dict[int, set[str]] = {}
    tag_rows = bind.execute(
        sa.select(poem_tags.c.poem_id, tags.c.name)
        .select_from(poem_tags.join(tags, tags.c.id == poem_tags.c.tag_id))
        .order_by(poem_tags.c.poem_id, tags.c.name)
    ).all()
    for poem_id, tag_name in tag_rows:
        tags_by_poem.setdefault(poem_id, set()).add(tag_name)

    rows: list[dict[str, Any]] = []
    poem_rows = bind.execute(
        sa.select(
            poems.c.id,
            poems.c.title,
            poems.c.author_id,
            poems.c.dynasty_id,
            poems.c.content,
            poems.c.normalized_content,
            poems.c.summary,
            poems.c.status,
            poems.c.version_no,
            poems.c.deleted_at,
        ).order_by(poems.c.id)
    ).mappings()
    for row in poem_rows:
        snapshot = {
            "title": row["title"],
            "author": {
                "id": row["author_id"],
                "name": author_names.get(row["author_id"]),
            },
            "dynasty": {
                "id": row["dynasty_id"],
                "name": dynasty_names.get(row["dynasty_id"]),
            },
            "content": row["content"],
            "normalized_content": row["normalized_content"],
            "summary": row["summary"],
            "status": row["status"],
            "version_no": row["version_no"],
            "deleted_at": (
                row["deleted_at"].isoformat() if row["deleted_at"] is not None else None
            ),
            "categories": categories_by_poem.get(row["id"], []),
            "tags": sorted(tags_by_poem.get(row["id"], set())),
        }
        canonical_snapshot = _canonical_snapshot(snapshot)
        rows.append(
            {
                "poem_id": row["id"],
                "source_id": None,
                "version_no": row["version_no"],
                "snapshot": snapshot,
                "content_hash": hashlib.sha256(
                    canonical_snapshot.encode("utf-8")
                ).hexdigest(),
                "change_type": "backfill",
                "changed_by_id": None,
                "change_note": "Migrated from the current poem state",
            }
        )

    if rows:
        bind.execute(poem_versions.insert(), rows)


def upgrade() -> None:
    op.create_table(
        "poem_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("poem_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_type",
            sa.String(length=30),
            server_default="manual",
            nullable=False,
        ),
        sa.Column(
            "source_key",
            sa.String(length=100),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("source_name", sa.String(length=200), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("raw_title", sa.String(length=255), nullable=True),
        sa.Column("raw_author_name", sa.String(length=120), nullable=True),
        sa.Column("raw_dynasty_name", sa.String(length=80), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("license_note", sa.String(length=500), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["poem_id"],
            ["poems.id"],
            name=op.f("fk_poem_sources_poem_id_poems"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poem_sources")),
        sa.UniqueConstraint(
            "source_key",
            "external_id",
            name="uq_poem_sources_source_key_external_id",
        ),
    )
    op.create_index(
        op.f("ix_poem_sources_poem_id"),
        "poem_sources",
        ["poem_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_sources_source_type"),
        "poem_sources",
        ["source_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_sources_content_hash"),
        "poem_sources",
        ["content_hash"],
        unique=False,
    )

    op.create_table(
        "poem_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("poem_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("change_type", sa.String(length=30), nullable=False),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column("change_note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_id"],
            ["users.id"],
            name=op.f("fk_poem_versions_changed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["poem_id"],
            ["poems.id"],
            name=op.f("fk_poem_versions_poem_id_poems"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["poem_sources.id"],
            name=op.f("fk_poem_versions_source_id_poem_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poem_versions")),
        sa.UniqueConstraint(
            "poem_id",
            "version_no",
            name="uq_poem_versions_poem_id_version_no",
        ),
    )
    op.create_index(
        op.f("ix_poem_versions_source_id"),
        "poem_versions",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_versions_content_hash"),
        "poem_versions",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_versions_change_type"),
        "poem_versions",
        ["change_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_versions_changed_by_id"),
        "poem_versions",
        ["changed_by_id"],
        unique=False,
    )

    op.create_table(
        "poem_annotations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("poem_version_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("annotation_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["poem_version_id"],
            ["poem_versions.id"],
            name=op.f("fk_poem_annotations_poem_version_id_poem_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["poem_sources.id"],
            name=op.f("fk_poem_annotations_source_id_poem_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poem_annotations")),
    )
    op.create_index(
        op.f("ix_poem_annotations_poem_version_id"),
        "poem_annotations",
        ["poem_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_annotations_source_id"),
        "poem_annotations",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_annotations_annotation_type"),
        "poem_annotations",
        ["annotation_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_annotations_status"),
        "poem_annotations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_annotations_content_hash"),
        "poem_annotations",
        ["content_hash"],
        unique=False,
    )

    op.create_table(
        "poem_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("poem_id", sa.Integer(), nullable=False),
        sa.Column("poem_version_id", sa.Integer(), nullable=False),
        sa.Column("annotation_id", sa.Integer(), nullable=True),
        sa.Column("granularity", sa.String(length=20), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("vector_id", sa.String(length=100), nullable=True),
        sa.Column("embedding_model", sa.String(length=150), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column(
            "chunk_strategy",
            sa.String(length=100),
            server_default="structural-v1",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["annotation_id"],
            ["poem_annotations.id"],
            name=op.f("fk_poem_chunks_annotation_id_poem_annotations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["poem_id"],
            ["poems.id"],
            name=op.f("fk_poem_chunks_poem_id_poems"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["poem_version_id"],
            ["poem_versions.id"],
            name=op.f("fk_poem_chunks_poem_version_id_poem_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poem_chunks")),
        sa.UniqueConstraint(
            "poem_version_id",
            "granularity",
            "chunk_strategy",
            "chunk_index",
            name="uq_poem_chunks_version_granularity_strategy_index",
        ),
        sa.UniqueConstraint("vector_id", name=op.f("uq_poem_chunks_vector_id")),
    )
    op.create_index(
        op.f("ix_poem_chunks_poem_id"),
        "poem_chunks",
        ["poem_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_chunks_poem_version_id"),
        "poem_chunks",
        ["poem_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_chunks_annotation_id"),
        "poem_chunks",
        ["annotation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_chunks_granularity"),
        "poem_chunks",
        ["granularity"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_chunks_content_hash"),
        "poem_chunks",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_chunks_status"),
        "poem_chunks",
        ["status"],
        unique=False,
    )

    _backfill_poem_versions()


def downgrade() -> None:
    op.drop_table("poem_chunks")
    op.drop_table("poem_annotations")
    op.drop_table("poem_versions")
    op.drop_table("poem_sources")
