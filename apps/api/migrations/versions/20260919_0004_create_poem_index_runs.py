"""Create poem index run metadata.

Revision ID: 20260919_0004
Revises: 20260919_0003
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260919_0004"
down_revision: str | None = "20260919_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "poem_index_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("poem_version_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.String(length=20),
            server_default="chunk",
            nullable=False,
        ),
        sa.Column("chunk_strategy", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=150), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("vector_collection", sa.String(length=150), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "chunk_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "embedded_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_poem_index_runs_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["poem_version_id"],
            ["poem_versions.id"],
            name=op.f("fk_poem_index_runs_poem_version_id_poem_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poem_index_runs")),
    )
    op.create_index(
        op.f("ix_poem_index_runs_poem_version_id"),
        "poem_index_runs",
        ["poem_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_index_runs_status"),
        "poem_index_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_poem_index_runs_created_by_id"),
        "poem_index_runs",
        ["created_by_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("poem_index_runs")
