from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from math import ceil
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain_label import (
    DomainLabel,
    DomainLabelAlias,
    DomainLabelDimension,
    DomainLabelGenerationMethod,
    DomainLabelReviewStatus,
    DomainLabelStatus,
    PoemVersionDomainLabel,
)
from app.models.poem import Poem, PoemStatus
from app.models.version import PoemVersion
from app.schemas.domain_label_evaluation import (
    DomainLabelAuditReport,
    DomainLabelAuditScope,
    DomainLabelCoverageSummary,
    DomainLabelDimensionCoverage,
    DomainLabelDistributionSummary,
    DomainLabelGovernanceRisks,
    DomainLabelNumericDistribution,
    DomainLabelReviewSummary,
)

DIMENSIONS = tuple(item.value for item in DomainLabelDimension)
REVIEW_STATUSES = tuple(item.value for item in DomainLabelReviewStatus)
GENERATION_METHODS = tuple(item.value for item in DomainLabelGenerationMethod)
LABEL_STATUSES = tuple(item.value for item in DomainLabelStatus)


class DomainLabelAuditor:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def audit(self) -> DomainLabelAuditReport:
        published_poem_ids = set(
            (
                await self.session.scalars(
                    select(Poem.id).where(
                        Poem.status == PoemStatus.PUBLISHED.value,
                        Poem.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        current_versions = await self._load_current_versions(published_poem_ids)
        labels = list((await self.session.scalars(select(DomainLabel))).all())
        labels_by_id = {label.id: label for label in labels}
        assignments = await self._load_assignments()

        current_assignments = [
            row
            for row in assignments
            if self._is_current_published_assignment(row, current_versions)
        ]
        coverage, review, distributions = self._summarize_current_assignments(
            published_poem_ids=published_poem_ids,
            labels=labels,
            labels_by_id=labels_by_id,
            assignments=current_assignments,
        )
        risks = await self._summarize_risks(
            published_poem_ids=published_poem_ids,
            current_versions=current_versions,
            labels=labels,
            labels_by_id=labels_by_id,
            assignments=assignments,
        )
        return DomainLabelAuditReport(
            generated_at=datetime.now(UTC),
            scope=DomainLabelAuditScope(
                published_poem_count=len(published_poem_ids),
                current_version_count=len(current_versions),
                published_poems_without_current_version=(
                    len(published_poem_ids) - len(current_versions)
                ),
            ),
            coverage=coverage,
            review=review,
            distributions=distributions,
            risks=risks,
        )

    async def _load_current_versions(
        self,
        published_poem_ids: set[int],
    ) -> dict[int, int]:
        if not published_poem_ids:
            return {}
        rows = await self.session.execute(
            select(PoemVersion.poem_id, PoemVersion.id)
            .join(Poem, Poem.id == PoemVersion.poem_id)
            .where(
                Poem.id.in_(published_poem_ids),
                Poem.version_no == PoemVersion.version_no,
            )
        )
        return {poem_id: version_id for poem_id, version_id in rows}

    async def _load_assignments(self) -> list[Any]:
        rows = await self.session.execute(
            select(
                PoemVersionDomainLabel.id,
                PoemVersionDomainLabel.poem_version_id,
                PoemVersionDomainLabel.domain_label_id,
                PoemVersionDomainLabel.source_id,
                PoemVersionDomainLabel.generation_method,
                PoemVersionDomainLabel.origin_ref,
                PoemVersionDomainLabel.review_status,
                PoemVersionDomainLabel.model_name,
                PoemVersionDomainLabel.task_version,
                Poem.id.label("poem_id"),
                Poem.status.label("poem_status"),
                Poem.deleted_at.label("poem_deleted_at"),
                Poem.version_no.label("poem_version_no"),
                PoemVersion.version_no.label("assignment_version_no"),
            )
            .join(
                PoemVersion,
                PoemVersion.id == PoemVersionDomainLabel.poem_version_id,
            )
            .join(Poem, Poem.id == PoemVersion.poem_id)
        )
        return list(rows)

    @staticmethod
    def _is_current_published_assignment(
        row: Any,
        current_versions: dict[int, int],
    ) -> bool:
        return (
            row.poem_status == PoemStatus.PUBLISHED.value
            and row.poem_deleted_at is None
            and current_versions.get(row.poem_id) == row.poem_version_id
        )

    def _summarize_current_assignments(
        self,
        *,
        published_poem_ids: set[int],
        labels: list[DomainLabel],
        labels_by_id: dict[int, DomainLabel],
        assignments: list[Any],
    ) -> tuple[
        DomainLabelCoverageSummary,
        DomainLabelReviewSummary,
        DomainLabelDistributionSummary,
    ]:
        any_assignment_by_poem: dict[int, set[int]] = defaultdict(set)
        approved_by_poem: dict[int, set[int]] = defaultdict(set)
        online_labels_by_poem: dict[int, set[int]] = defaultdict(set)
        covered_poems_by_dimension = {
            dimension: set() for dimension in DIMENSIONS
        }
        online_labels_by_dimension = {
            dimension: set() for dimension in DIMENSIONS
        }
        approved_assignments_by_dimension = Counter()
        review_status_counts = Counter()
        generation_method_counts = Counter()
        status_by_generation = {
            method: Counter() for method in GENERATION_METHODS
        }

        for row in assignments:
            review_status_counts[row.review_status] += 1
            generation_method_counts[row.generation_method] += 1
            status_by_generation[row.generation_method][row.review_status] += 1
            any_assignment_by_poem[row.poem_id].add(row.id)

            if row.review_status != DomainLabelReviewStatus.APPROVED.value:
                continue

            approved_by_poem[row.poem_id].add(row.id)
            source_label = labels_by_id.get(row.domain_label_id)
            if source_label is None:
                continue
            approved_assignments_by_dimension[source_label.dimension] += 1
            resolved_label = self._resolve_online_label(source_label, labels_by_id)
            if resolved_label is None:
                continue
            online_labels_by_poem[row.poem_id].add(resolved_label.id)
            covered_poems_by_dimension[resolved_label.dimension].add(row.poem_id)
            online_labels_by_dimension[resolved_label.dimension].add(
                resolved_label.id
            )

        denominator = len(published_poem_ids)
        dimension_coverage: dict[str, DomainLabelDimensionCoverage] = {}
        for dimension in DIMENSIONS:
            active_labels = [
                label
                for label in labels
                if label.dimension == dimension
                and label.status == DomainLabelStatus.ACTIVE.value
            ]
            covered_poem_count = len(covered_poems_by_dimension[dimension])
            online_count = sum(
                len(online_labels_by_poem.get(poem_id, set()))
                for poem_id in covered_poems_by_dimension[dimension]
            )
            dimension_coverage[dimension] = DomainLabelDimensionCoverage(
                dimension=dimension,
                total_label_count=sum(
                    label.dimension == dimension for label in labels
                ),
                active_label_count=len(active_labels),
                approved_assignment_count=approved_assignments_by_dimension[
                    dimension
                ],
                online_label_count=len(online_labels_by_dimension[dimension]),
                covered_poem_count=covered_poem_count,
                coverage_rate=_rounded_ratio(covered_poem_count, denominator),
                average_online_label_count_per_covered_poem=_rounded_ratio(
                    online_count,
                    covered_poem_count,
                ),
            )

        current_online_poems = {
            poem_id
            for poem_id, label_ids in online_labels_by_poem.items()
            if label_ids
        }
        coverage = DomainLabelCoverageSummary(
            current_poems_with_any_assignment=len(any_assignment_by_poem),
            current_poems_with_any_approved_assignment=len(approved_by_poem),
            current_poems_with_online_visible_label=len(current_online_poems),
            current_poems_without_any_assignment=(
                denominator - len(any_assignment_by_poem)
            ),
            current_poems_without_online_visible_label=(
                denominator - len(current_online_poems)
            ),
            any_assignment_coverage_rate=_rounded_ratio(
                len(any_assignment_by_poem),
                denominator,
            ),
            approved_assignment_coverage_rate=_rounded_ratio(
                len(approved_by_poem),
                denominator,
            ),
            online_visible_coverage_rate=_rounded_ratio(
                len(current_online_poems),
                denominator,
            ),
            dimensions=dimension_coverage,
        )
        review = DomainLabelReviewSummary(
            current_assignment_count=len(assignments),
            review_status_counts=_counter_dict(
                review_status_counts,
                REVIEW_STATUSES,
            ),
            generation_method_counts=_counter_dict(
                generation_method_counts,
                GENERATION_METHODS,
            ),
            review_status_by_generation_method={
                method: _counter_dict(status_by_generation[method], REVIEW_STATUSES)
                for method in GENERATION_METHODS
            },
            label_status_counts=_counter_dict(
                Counter(label.status for label in labels),
                LABEL_STATUSES,
            ),
        )
        distributions = DomainLabelDistributionSummary(
            current_assignments_per_poem=_distribution(
                [
                    len(any_assignment_by_poem.get(poem_id, set()))
                    for poem_id in published_poem_ids
                ]
            ),
            approved_assignments_per_poem=_distribution(
                [
                    len(approved_by_poem.get(poem_id, set()))
                    for poem_id in published_poem_ids
                ]
            ),
            online_visible_labels_per_poem=_distribution(
                [
                    len(online_labels_by_poem.get(poem_id, set()))
                    for poem_id in published_poem_ids
                ]
            ),
        )
        return coverage, review, distributions

    async def _summarize_risks(
        self,
        *,
        published_poem_ids: set[int],
        current_versions: dict[int, int],
        labels: list[DomainLabel],
        labels_by_id: dict[int, DomainLabel],
        assignments: list[Any],
    ) -> DomainLabelGovernanceRisks:
        online_visible_label_ids: set[int] = set()
        approved_merged_count = 0
        approved_deprecated_count = 0
        for row in assignments:
            if not self._is_current_published_assignment(row, current_versions):
                continue
            if row.review_status != DomainLabelReviewStatus.APPROVED.value:
                continue
            source_label = labels_by_id.get(row.domain_label_id)
            if source_label is None:
                continue
            resolved_label = self._resolve_online_label(source_label, labels_by_id)
            if resolved_label is not None:
                online_visible_label_ids.add(resolved_label.id)
            if source_label.status == DomainLabelStatus.MERGED.value:
                approved_merged_count += 1
            elif source_label.status == DomainLabelStatus.DEPRECATED.value:
                approved_deprecated_count += 1

        non_current_count = sum(
            not self._is_current_published_assignment(row, current_versions)
            for row in assignments
        )
        unpublished_or_deleted_count = sum(
            row.poem_status != PoemStatus.PUBLISHED.value
            or row.poem_deleted_at is not None
            for row in assignments
        )
        source_metadata_risks = sum(
            row.generation_method
            in {
                DomainLabelGenerationMethod.PUBLIC_DATASET.value,
                DomainLabelGenerationMethod.AI.value,
            }
            and row.source_id is None
            for row in assignments
        )
        ai_metadata_risks = sum(
            row.generation_method == DomainLabelGenerationMethod.AI.value
            and (not row.model_name or not row.task_version)
            for row in assignments
        )
        origin_ref_mismatches = sum(
            row.origin_ref.split(":", 1)[0] != row.generation_method
            for row in assignments
        )
        active_without_assignments = sum(
            label.status == DomainLabelStatus.ACTIVE.value
            and label.id not in online_visible_label_ids
            for label in labels
        )
        return DomainLabelGovernanceRisks(
            non_current_version_assignments=non_current_count,
            unpublished_or_deleted_poem_assignments=unpublished_or_deleted_count,
            approved_assignments_with_merged_labels=approved_merged_count,
            approved_assignments_with_deprecated_labels=approved_deprecated_count,
            active_labels_without_online_visible_assignments=(
                active_without_assignments
            ),
            public_or_ai_assignments_without_source=source_metadata_risks,
            ai_assignments_missing_metadata=ai_metadata_risks,
            invalid_merge_targets=self._count_invalid_merge_targets(
                labels,
                labels_by_id,
            ),
            origin_ref_generation_method_mismatches=origin_ref_mismatches,
            cross_dimension_aliases=await self._count_cross_dimension_aliases(),
        )

    @staticmethod
    def _resolve_online_label(
        label: DomainLabel,
        labels_by_id: dict[int, DomainLabel],
    ) -> DomainLabel | None:
        if label.status == DomainLabelStatus.ACTIVE.value:
            return label
        if label.status != DomainLabelStatus.MERGED.value:
            return None
        target = labels_by_id.get(label.merged_into_id or 0)
        if (
            target is None
            or target.id == label.id
            or target.dimension != label.dimension
            or target.status != DomainLabelStatus.ACTIVE.value
        ):
            return None
        return target

    @staticmethod
    def _count_invalid_merge_targets(
        labels: list[DomainLabel],
        labels_by_id: dict[int, DomainLabel],
    ) -> int:
        invalid = 0
        for label in labels:
            if label.status != DomainLabelStatus.MERGED.value:
                continue
            target = labels_by_id.get(label.merged_into_id or 0)
            if (
                target is None
                or target.id == label.id
                or target.dimension != label.dimension
                or target.status != DomainLabelStatus.ACTIVE.value
            ):
                invalid += 1
        return invalid

    async def _count_cross_dimension_aliases(self) -> int:
        rows = await self.session.execute(
            select(
                DomainLabelAlias.normalized_alias,
                DomainLabel.dimension,
                DomainLabel.id,
            )
            .join(
                DomainLabel,
                DomainLabel.id == DomainLabelAlias.domain_label_id,
            )
            .where(DomainLabel.status == DomainLabelStatus.ACTIVE.value)
        )
        dimensions_by_alias: dict[str, set[str]] = defaultdict(set)
        for normalized_alias, dimension, _ in rows:
            dimensions_by_alias[normalized_alias].add(dimension)
        return sum(len(dimensions) > 1 for dimensions in dimensions_by_alias.values())


def format_domain_label_audit_report(report: DomainLabelAuditReport) -> str:
    lines = [
        f"generated_at={report.generated_at.isoformat()}",
        (
            f"scope published_poems={report.scope.published_poem_count} "
            f"current_versions={report.scope.current_version_count} "
            "published_poems_without_current_version="
            f"{report.scope.published_poems_without_current_version}"
        ),
        (
            "coverage "
            f"any_assignment={report.coverage.current_poems_with_any_assignment} "
            f"approved={report.coverage.current_poems_with_any_approved_assignment} "
            "online_visible="
            f"{report.coverage.current_poems_with_online_visible_label} "
            "online_rate="
            f"{report.coverage.online_visible_coverage_rate:.6f}"
        ),
        "dimensions:",
    ]
    for dimension in DIMENSIONS:
        item = report.coverage.dimensions[dimension]
        lines.append(
            f"- {dimension}: labels={item.active_label_count}/"
            f"{item.total_label_count} online_labels={item.online_label_count} "
            f"covered_poems={item.covered_poem_count} "
            f"coverage_rate={item.coverage_rate:.6f} "
            "average_online_labels_per_covered_poem="
            f"{item.average_online_label_count_per_covered_poem:.6f}"
        )

    lines.extend(
        [
            (
                "review "
                f"current_assignments={report.review.current_assignment_count} "
                + " ".join(
                    f"{status}={report.review.review_status_counts[status]}"
                    for status in REVIEW_STATUSES
                )
            ),
            (
                "generation_methods "
                + " ".join(
                    f"{method}={report.review.generation_method_counts[method]}"
                    for method in GENERATION_METHODS
                )
            ),
            (
                "label_statuses "
                + " ".join(
                    f"{status}={report.review.label_status_counts[status]}"
                    for status in LABEL_STATUSES
                )
            ),
            (
                "distributions "
                "current_assignments_per_poem="
                f"{_format_distribution(report.distributions.current_assignments_per_poem)} "
                "approved_assignments_per_poem="
                f"{_format_distribution(report.distributions.approved_assignments_per_poem)} "
                "online_visible_labels_per_poem="
                f"{_format_distribution(report.distributions.online_visible_labels_per_poem)}"
            ),
            (
                "risks "
                f"non_current_version_assignments="
                f"{report.risks.non_current_version_assignments} "
                "unpublished_or_deleted_poem_assignments="
                f"{report.risks.unpublished_or_deleted_poem_assignments} "
                "approved_assignments_with_merged_labels="
                f"{report.risks.approved_assignments_with_merged_labels} "
                "approved_assignments_with_deprecated_labels="
                f"{report.risks.approved_assignments_with_deprecated_labels}"
            ),
            (
                "risks_continued "
                "active_labels_without_online_visible_assignments="
                f"{report.risks.active_labels_without_online_visible_assignments} "
                "public_or_ai_assignments_without_source="
                f"{report.risks.public_or_ai_assignments_without_source} "
                "ai_assignments_missing_metadata="
                f"{report.risks.ai_assignments_missing_metadata} "
                f"invalid_merge_targets={report.risks.invalid_merge_targets}"
            ),
            (
                "risks_metadata "
                "origin_ref_generation_method_mismatches="
                f"{report.risks.origin_ref_generation_method_mismatches} "
                f"cross_dimension_aliases={report.risks.cross_dimension_aliases}"
            ),
        ]
    )
    return "\n".join(lines)


def _counter_dict(counter: Counter[str], keys: tuple[str, ...]) -> dict[str, int]:
    return {key: int(counter.get(key, 0)) for key in keys}


def _distribution(values: list[int]) -> DomainLabelNumericDistribution:
    if not values:
        return DomainLabelNumericDistribution(
            minimum=0,
            maximum=0,
            mean=0.0,
            p50=0.0,
            p90=0.0,
        )
    return DomainLabelNumericDistribution(
        minimum=min(values),
        maximum=max(values),
        mean=round(statistics.fmean(values), 6),
        p50=_percentile(values, 0.50),
        p90=_percentile(values, 0.90),
    )


def _percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, ceil(fraction * len(ordered)) - 1)
    return float(ordered[index])


def _rounded_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _format_distribution(distribution: DomainLabelNumericDistribution) -> str:
    return (
        f"min={distribution.minimum} max={distribution.maximum} "
        f"mean={distribution.mean:.6f} p50={distribution.p50:.6f} "
        f"p90={distribution.p90:.6f}"
    )
