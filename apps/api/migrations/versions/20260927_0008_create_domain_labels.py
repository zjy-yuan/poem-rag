"""Create version-scoped domain label tables.

Revision ID: 20260927_0008
Revises: 20260927_0007
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0008"
down_revision: str | None = "20260927_0007"
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
        "domain_labels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dimension", sa.String(length=30), nullable=False),
        sa.Column("canonical_name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "dimension IN ('imagery', 'emotion', 'theme', 'allusion')",
            name=op.f("ck_domain_labels_valid_dimension"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'merged', 'deprecated')",
            name=op.f("ck_domain_labels_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["domain_labels.id"],
            name=op.f("fk_domain_labels_merged_into_id_domain_labels"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_labels")),
        sa.UniqueConstraint(
            "dimension",
            "normalized_name",
            name="uq_domain_labels_dimension_normalized_name",
        ),
    )
    op.create_index(
        op.f("ix_domain_labels_merged_into_id"),
        "domain_labels",
        ["merged_into_id"],
        unique=False,
    )
    op.create_index(
        "ix_domain_labels_dimension_status",
        "domain_labels",
        ["dimension", "status"],
        unique=False,
    )

    op.create_table(
        "domain_label_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("domain_label_id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=80), nullable=False),
        sa.Column("normalized_alias", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "alias <> ''",
            name=op.f("ck_domain_label_aliases_non_empty_alias"),
        ),
        sa.CheckConstraint(
            "normalized_alias <> ''",
            name=op.f("ck_domain_label_aliases_non_empty_normalized_alias"),
        ),
        sa.ForeignKeyConstraint(
            ["domain_label_id"],
            ["domain_labels.id"],
            name=op.f("fk_domain_label_aliases_domain_label_id_domain_labels"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["poem_sources.id"],
            name=op.f("fk_domain_label_aliases_source_id_poem_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_domain_label_aliases")),
        sa.UniqueConstraint(
            "domain_label_id",
            "normalized_alias",
            name="uq_domain_label_aliases_label_normalized_alias",
        ),
    )
    op.create_index(
        op.f("ix_domain_label_aliases_domain_label_id"),
        "domain_label_aliases",
        ["domain_label_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_domain_label_aliases_normalized_alias"),
        "domain_label_aliases",
        ["normalized_alias"],
        unique=False,
    )
    op.create_index(
        op.f("ix_domain_label_aliases_source_id"),
        "domain_label_aliases",
        ["source_id"],
        unique=False,
    )

    op.create_table(
        "poem_version_domain_labels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("poem_version_id", sa.Integer(), nullable=False),
        sa.Column("domain_label_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("generation_method", sa.String(length=30), nullable=False),
        sa.Column("origin_ref", sa.String(length=120), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.String(length=150), nullable=True),
        sa.Column("task_version", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "generation_method IN ('manual', 'public_dataset', 'ai')",
            name=op.f("ck_poem_version_domain_labels_valid_generation_method"),
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'archived')",
            name=op.f("ck_poem_version_domain_labels_valid_review_status"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_poem_version_domain_labels_valid_confidence"),
        ),
        sa.CheckConstraint(
            "line_start IS NULL OR line_start >= 0",
            name=op.f("ck_poem_version_domain_labels_valid_line_start"),
        ),
        sa.CheckConstraint(
            "line_end IS NULL OR line_end >= 0",
            name=op.f("ck_poem_version_domain_labels_valid_line_end"),
        ),
        sa.CheckConstraint(
            "line_start IS NULL OR line_end IS NULL OR line_end >= line_start",
            name=op.f("ck_poem_version_domain_labels_valid_line_range"),
        ),
        sa.CheckConstraint(
            "origin_ref <> ''",
            name=op.f("ck_poem_version_domain_labels_non_empty_origin_ref"),
        ),
        sa.CheckConstraint(
            "generation_method <> 'ai' OR (model_name IS NOT NULL AND task_version IS NOT NULL)",
            name=op.f("ck_poem_version_domain_labels_ai_requires_model_and_task"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_poem_version_domain_labels_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["domain_label_id"],
            ["domain_labels.id"],
            name=op.f("fk_poem_version_domain_labels_domain_label_id_domain_labels"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["poem_version_id"],
            ["poem_versions.id"],
            name=op.f("fk_poem_version_domain_labels_poem_version_id_poem_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["users.id"],
            name=op.f("fk_poem_version_domain_labels_reviewed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["poem_sources.id"],
            name=op.f("fk_poem_version_domain_labels_source_id_poem_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poem_version_domain_labels")),
        sa.UniqueConstraint(
            "poem_version_id",
            "domain_label_id",
            "origin_ref",
            name="uq_poem_version_domain_labels_version_label_origin",
        ),
    )
    op.create_index(
        op.f("ix_poem_version_domain_labels_poem_version_id"),
        "poem_version_domain_labels",
        ["poem_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_version_domain_labels_domain_label_id"),
        "poem_version_domain_labels",
        ["domain_label_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_version_domain_labels_source_id"),
        "poem_version_domain_labels",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_version_domain_labels_created_by_id"),
        "poem_version_domain_labels",
        ["created_by_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_version_domain_labels_reviewed_by_id"),
        "poem_version_domain_labels",
        ["reviewed_by_id"],
        unique=False,
    )
    op.create_index(
        "ix_poem_version_domain_labels_version_review",
        "poem_version_domain_labels",
        ["poem_version_id", "review_status"],
        unique=False,
    )
    op.create_index(
        "ix_poem_version_domain_labels_label_review_method",
        "poem_version_domain_labels",
        ["domain_label_id", "review_status", "generation_method"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("poem_version_domain_labels")
    op.drop_table("domain_label_aliases")
    op.drop_table("domain_labels")
