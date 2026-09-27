from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.source import PoemSource
    from app.models.user import User
    from app.models.version import PoemVersion


class DomainLabelDimension(StrEnum):
    IMAGERY = "imagery"
    EMOTION = "emotion"
    THEME = "theme"
    ALLUSION = "allusion"


class DomainLabelStatus(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"
    DEPRECATED = "deprecated"


class DomainLabelGenerationMethod(StrEnum):
    MANUAL = "manual"
    PUBLIC_DATASET = "public_dataset"
    AI = "ai"


class DomainLabelReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class DomainLabel(Base, TimestampMixin):
    __tablename__ = "domain_labels"
    __table_args__ = (
        UniqueConstraint(
            "dimension",
            "normalized_name",
            name="uq_domain_labels_dimension_normalized_name",
        ),
        Index("ix_domain_labels_dimension_status", "dimension", "status"),
        CheckConstraint(
            "dimension IN ('imagery', 'emotion', 'theme', 'allusion')",
            name="valid_dimension",
        ),
        CheckConstraint(
            "status IN ('active', 'merged', 'deprecated')",
            name="valid_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dimension: Mapped[str] = mapped_column(String(30), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=DomainLabelStatus.ACTIVE.value,
        server_default=DomainLabelStatus.ACTIVE.value,
        nullable=False,
    )
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("domain_labels.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    merged_into: Mapped[DomainLabel | None] = relationship(
        remote_side="DomainLabel.id",
        back_populates="merged_children",
    )
    merged_children: Mapped[list[DomainLabel]] = relationship(
        back_populates="merged_into",
    )
    aliases: Mapped[list[DomainLabelAlias]] = relationship(
        back_populates="domain_label",
        cascade="all, delete-orphan",
    )
    version_links: Mapped[list[PoemVersionDomainLabel]] = relationship(
        back_populates="domain_label",
    )


class DomainLabelAlias(Base):
    __tablename__ = "domain_label_aliases"
    __table_args__ = (
        UniqueConstraint(
            "domain_label_id",
            "normalized_alias",
            name="uq_domain_label_aliases_label_normalized_alias",
        ),
        CheckConstraint("alias <> ''", name="non_empty_alias"),
        CheckConstraint("normalized_alias <> ''", name="non_empty_normalized_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain_label_id: Mapped[int] = mapped_column(
        ForeignKey("domain_labels.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("poem_sources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    domain_label: Mapped[DomainLabel] = relationship(back_populates="aliases")
    source: Mapped[PoemSource | None] = relationship()


class PoemVersionDomainLabel(Base, TimestampMixin):
    __tablename__ = "poem_version_domain_labels"
    __table_args__ = (
        UniqueConstraint(
            "poem_version_id",
            "domain_label_id",
            "origin_ref",
            name="uq_poem_version_domain_labels_version_label_origin",
        ),
        Index(
            "ix_poem_version_domain_labels_version_review",
            "poem_version_id",
            "review_status",
        ),
        Index(
            "ix_poem_version_domain_labels_label_review_method",
            "domain_label_id",
            "review_status",
            "generation_method",
        ),
        CheckConstraint(
            "generation_method IN ('manual', 'public_dataset', 'ai')",
            name="valid_generation_method",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'archived')",
            name="valid_review_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="valid_confidence",
        ),
        CheckConstraint(
            "line_start IS NULL OR line_start >= 0",
            name="valid_line_start",
        ),
        CheckConstraint(
            "line_end IS NULL OR line_end >= 0",
            name="valid_line_end",
        ),
        CheckConstraint(
            "line_start IS NULL OR line_end IS NULL OR line_end >= line_start",
            name="valid_line_range",
        ),
        CheckConstraint("origin_ref <> ''", name="non_empty_origin_ref"),
        CheckConstraint(
            "generation_method <> 'ai' OR (model_name IS NOT NULL AND task_version IS NOT NULL)",
            name="ai_requires_model_and_task",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poem_version_id: Mapped[int] = mapped_column(
        ForeignKey("poem_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    domain_label_id: Mapped[int] = mapped_column(
        ForeignKey("domain_labels.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("poem_sources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    generation_method: Mapped[str] = mapped_column(String(30), nullable=False)
    origin_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(20),
        default=DomainLabelReviewStatus.PENDING.value,
        server_default=DomainLabelReviewStatus.PENDING.value,
        nullable=False,
    )
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    task_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    version: Mapped[PoemVersion] = relationship(back_populates="domain_labels")
    domain_label: Mapped[DomainLabel] = relationship(back_populates="version_links")
    source: Mapped[PoemSource | None] = relationship()
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    reviewed_by: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id])
