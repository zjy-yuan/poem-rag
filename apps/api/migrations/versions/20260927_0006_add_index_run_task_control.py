"""Add index run task control metadata.

Revision ID: 20260927_0006
Revises: 20260920_0005
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0006"
down_revision: str | None = "20260920_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "poem_index_runs",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column("celery_task_id", sa.String(length=155), nullable=True),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default="3",
            nullable=False,
        ),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "poem_index_runs",
        sa.Column("error_code", sa.String(length=80), nullable=True),
    )
    op.create_index(
        op.f("ix_poem_index_runs_idempotency_key"),
        "poem_index_runs",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_poem_index_runs_celery_task_id"),
        "poem_index_runs",
        ["celery_task_id"],
        unique=False,
    )
    op.create_index(
        "ix_poem_index_runs_status_lease_expires_at",
        "poem_index_runs",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_poem_index_runs_status_lease_expires_at",
        table_name="poem_index_runs",
    )
    op.drop_index(
        op.f("ix_poem_index_runs_celery_task_id"),
        table_name="poem_index_runs",
    )
    op.drop_index(
        op.f("ix_poem_index_runs_idempotency_key"),
        table_name="poem_index_runs",
    )
    op.drop_column("poem_index_runs", "error_code")
    op.drop_column("poem_index_runs", "cancel_requested_at")
    op.drop_column("poem_index_runs", "heartbeat_at")
    op.drop_column("poem_index_runs", "lease_expires_at")
    op.drop_column("poem_index_runs", "lease_owner")
    op.drop_column("poem_index_runs", "max_attempts")
    op.drop_column("poem_index_runs", "attempt_count")
    op.drop_column("poem_index_runs", "celery_task_id")
    op.drop_column("poem_index_runs", "idempotency_key")
