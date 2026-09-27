"""Add the active index publication pointer.

``poems.active_index_run_id`` points at the run whose vectors are currently
published for that poem. It intentionally has no foreign key: the pointer is
only read through equality comparisons, run ids are never reused, and a
foreign key would create a ``poems -> poem_index_runs -> poem_versions ->
poems`` constraint cycle for no practical benefit.

``poem_chunks.index_run_id`` records which run wrote each chunk. The explicit
index is created before the foreign key so MySQL reuses it instead of creating
a second index for the constraint.

Revision ID: 20260927_0007
Revises: 20260927_0006
Create Date: 2026-09-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0007"
down_revision: str | None = "20260927_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "poems",
        sa.Column("active_index_run_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_poems_active_index_run_id"),
        "poems",
        ["active_index_run_id"],
        unique=False,
    )
    op.add_column(
        "poem_chunks",
        sa.Column("index_run_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_poem_chunks_index_run_id"),
        "poem_chunks",
        ["index_run_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_poem_chunks_index_run_id_poem_index_runs"),
        "poem_chunks",
        "poem_index_runs",
        ["index_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # MySQL reuses the explicit index as the foreign key's supporting index,
    # so the constraint has to go before the index can be dropped.
    op.drop_constraint(
        op.f("fk_poem_chunks_index_run_id_poem_index_runs"),
        "poem_chunks",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_poem_chunks_index_run_id"),
        table_name="poem_chunks",
    )
    op.drop_column("poem_chunks", "index_run_id")
    op.drop_index(
        op.f("ix_poems_active_index_run_id"),
        table_name="poems",
    )
    op.drop_column("poems", "active_index_run_id")
