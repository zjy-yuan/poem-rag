"""Create poem catalog tables.

Revision ID: 20260919_0002
Revises: 20260919_0001
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_0002"
down_revision: str | None = "20260919_0001"
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


def upgrade() -> None:
    op.create_table(
        "dynasties",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dynasties")),
    )
    op.create_index(
        op.f("ix_dynasties_normalized_name"),
        "dynasties",
        ["normalized_name"],
        unique=True,
    )

    op.create_table(
        "authors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("dynasty_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["dynasty_id"],
            ["dynasties.id"],
            name=op.f("fk_authors_dynasty_id_dynasties"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authors")),
    )
    op.create_index(
        op.f("ix_authors_normalized_name"),
        "authors",
        ["normalized_name"],
        unique=True,
    )
    op.create_index(op.f("ix_authors_dynasty_id"), "authors", ["dynasty_id"], unique=False)

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["categories.id"],
            name=op.f("fk_categories_parent_id_categories"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint(
            "normalized_name",
            "type",
            name="uq_categories_normalized_name_type",
        ),
    )
    op.create_index(
        op.f("ix_categories_normalized_name"),
        "categories",
        ["normalized_name"],
        unique=False,
    )
    op.create_index(op.f("ix_categories_type"), "categories", ["type"], unique=False)
    op.create_index(op.f("ix_categories_parent_id"), "categories", ["parent_id"], unique=False)

    op.create_table(
        "poems",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("dynasty_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized_content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("version_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["authors.id"],
            name=op.f("fk_poems_author_id_authors"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["dynasty_id"],
            ["dynasties.id"],
            name=op.f("fk_poems_dynasty_id_dynasties"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poems")),
    )
    op.create_index(op.f("ix_poems_title"), "poems", ["title"], unique=False)
    op.create_index(op.f("ix_poems_author_id"), "poems", ["author_id"], unique=False)
    op.create_index(op.f("ix_poems_dynasty_id"), "poems", ["dynasty_id"], unique=False)
    op.create_index(op.f("ix_poems_status"), "poems", ["status"], unique=False)
    op.create_index(op.f("ix_poems_deleted_at"), "poems", ["deleted_at"], unique=False)

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
    )
    op.create_index(
        op.f("ix_tags_normalized_name"),
        "tags",
        ["normalized_name"],
        unique=True,
    )

    op.create_table(
        "poem_categories",
        sa.Column("poem_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=30), server_default="manual", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_poem_categories_category_id_categories"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["poem_id"],
            ["poems.id"],
            name=op.f("fk_poem_categories_poem_id_poems"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("poem_id", "category_id", name=op.f("pk_poem_categories")),
    )

    op.create_table(
        "poem_tags",
        sa.Column("poem_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["poem_id"],
            ["poems.id"],
            name=op.f("fk_poem_tags_poem_id_poems"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name=op.f("fk_poem_tags_tag_id_tags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("poem_id", "tag_id", name=op.f("pk_poem_tags")),
    )


def downgrade() -> None:
    op.drop_table("poem_tags")
    op.drop_table("poem_categories")
    op.drop_index(op.f("ix_tags_normalized_name"), table_name="tags")
    op.drop_table("tags")
    op.drop_index(op.f("ix_poems_deleted_at"), table_name="poems")
    op.drop_index(op.f("ix_poems_status"), table_name="poems")
    op.drop_index(op.f("ix_poems_dynasty_id"), table_name="poems")
    op.drop_index(op.f("ix_poems_author_id"), table_name="poems")
    op.drop_index(op.f("ix_poems_title"), table_name="poems")
    op.drop_table("poems")
    op.drop_index(op.f("ix_categories_parent_id"), table_name="categories")
    op.drop_index(op.f("ix_categories_type"), table_name="categories")
    op.drop_index(op.f("ix_categories_normalized_name"), table_name="categories")
    op.drop_table("categories")
    op.drop_index(op.f("ix_authors_dynasty_id"), table_name="authors")
    op.drop_index(op.f("ix_authors_normalized_name"), table_name="authors")
    op.drop_table("authors")
    op.drop_index(op.f("ix_dynasties_normalized_name"), table_name="dynasties")
    op.drop_table("dynasties")
