from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
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
from app.models.source import PoemSource
from app.models.version import PoemVersion
from app.schemas.domain_label_gold import (
    DomainLabelDimensionMetric,
    DomainLabelGoldCoverageSummary,
    DomainLabelGoldDataset,
    DomainLabelGoldDimensionCoverage,
    DomainLabelGoldEvaluationReport,
    DomainLabelGoldEvidenceIssue,
    DomainLabelGoldExtraLabel,
    DomainLabelGoldLabel,
    DomainLabelGoldMissingLabel,
    DomainLabelGoldQualitySummary,
    DomainLabelGoldRecord,
    DomainLabelGoldRecordReport,
    DomainLabelMetricSummary,
    DomainLabelMetricValues,
)

DIMENSIONS = tuple(item.value for item in DomainLabelDimension)
_ASSIGNMENT_PRIORITY = {
    DomainLabelGenerationMethod.MANUAL.value: 0,
    DomainLabelGenerationMethod.PUBLIC_DATASET.value: 1,
    DomainLabelGenerationMethod.AI.value: 2,
}


class DomainLabelGoldEvaluator:
    """Evaluate approved online-visible labels against a frozen human standard."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def evaluate(
        self,
        dataset: DomainLabelGoldDataset,
    ) -> DomainLabelGoldEvaluationReport:
        published_poem_count = int(
            await self.session.scalar(
                select(func.count(Poem.id)).where(
                    Poem.status == PoemStatus.PUBLISHED.value,
                    Poem.deleted_at.is_(None),
                )
            )
            or 0
        )
        labels = list((await self.session.scalars(select(DomainLabel))).all())
        labels_by_id = {label.id: label for label in labels}
        aliases = list((await self.session.scalars(select(DomainLabelAlias))).all())
        active_label_ids_by_name = self._build_active_name_index(labels, aliases)

        gold_by_dimension: dict[str, set[tuple[str, int]]] = defaultdict(set)
        predicted_by_dimension: dict[str, set[tuple[str, int]]] = defaultdict(set)
        gold_records_by_dimension = {dimension: set() for dimension in DIMENSIONS}
        predicted_records_by_dimension = {
            dimension: set() for dimension in DIMENSIONS
        }
        record_reports: list[DomainLabelGoldRecordReport] = []
        approved_assignment_count = 0
        macro_plan_rates: list[float] = []
        total_plan_gap_count = 0

        for record in dataset.records:
            poem, version, source = await self._resolve_record(dataset, record)
            gold_by_label_id = self._resolve_gold_labels(
                record,
                active_label_ids_by_name,
                labels_by_id,
            )
            predicted, assignments = await self._load_predicted_labels(version.id, labels_by_id)
            approved_assignment_count += len(assignments)
            record_report = self._compare_record(
                record=record,
                poem=poem,
                version=version,
                gold_by_label_id=gold_by_label_id,
                predicted=predicted,
                labels_by_id=labels_by_id,
            )
            record_reports.append(record_report)

            gold_ids = set(gold_by_label_id)
            predicted_ids = set(predicted)
            for label_id in gold_ids:
                gold_by_dimension[labels_by_id[label_id].dimension].add(
                    (record.external_id, label_id)
                )
            for label_id in predicted_ids:
                predicted_by_dimension[labels_by_id[label_id].dimension].add(
                    (record.external_id, label_id)
                )
            for dimension in DIMENSIONS:
                if any(
                    labels_by_id[label_id].dimension == dimension
                    for label_id in gold_ids
                ):
                    gold_records_by_dimension[dimension].add(record.external_id)
                if any(
                    labels_by_id[label_id].dimension == dimension
                    for label_id in predicted_ids
                ):
                    predicted_records_by_dimension[dimension].add(record.external_id)

        metrics = self._build_metrics(gold_by_dimension, predicted_by_dimension)
        dimension_coverage: dict[str, DomainLabelGoldDimensionCoverage] = {}
        for dimension in DIMENSIONS:
            target_count = dataset.target_record_counts[DomainLabelDimension(dimension)]
            gold_count = len(gold_records_by_dimension[dimension])
            predicted_count = len(predicted_records_by_dimension[dimension])
            plan_coverage = (
                min(round(gold_count / target_count, 6), 1.0)
                if target_count
                else 1.0
            )
            plan_gap = max(target_count - gold_count, 0)
            if target_count:
                macro_plan_rates.append(plan_coverage)
                total_plan_gap_count += plan_gap
            dimension_coverage[dimension] = DomainLabelGoldDimensionCoverage(
                dimension=dimension,
                target_record_count=target_count,
                gold_record_count=gold_count,
                predicted_record_count=predicted_count,
                plan_coverage_rate=plan_coverage,
                plan_gap_count=plan_gap,
                predicted_in_scope_rate=_rounded_ratio(
                    predicted_count,
                    gold_count,
                ),
            )

        coverage = DomainLabelGoldCoverageSummary(
            published_poem_count=published_poem_count,
            gold_record_count=len(dataset.records),
            gold_scope_coverage_rate=_rounded_ratio(
                len(dataset.records),
                published_poem_count,
            ),
            records_with_predictions=sum(
                bool(record.predicted_label_count) for record in record_reports
            ),
            records_without_predictions=sum(
                not record.predicted_label_count for record in record_reports
            ),
            macro_plan_coverage_rate=(
                round(sum(macro_plan_rates) / len(macro_plan_rates), 6)
                if macro_plan_rates
                else 0.0
            ),
            total_plan_gap_count=total_plan_gap_count,
            dimensions=dimension_coverage,
        )
        evidence_error_count = sum(
            len(record.evidence_issues) for record in record_reports
        )
        critical_missing_label_count = sum(
            record.critical_missing_label_count for record in record_reports
        )
        critical_gold_label_count = sum(
            label.severity.value == "critical"
            for record in dataset.records
            for label in record.labels
        )
        gold_label_count = metrics.micro.gold_count
        quality = DomainLabelGoldQualitySummary(
            gold_label_count=gold_label_count,
            critical_gold_label_count=critical_gold_label_count,
            predicted_label_count=metrics.micro.predicted_count,
            true_positive=metrics.micro.true_positive,
            false_positive=metrics.micro.false_positive,
            false_negative=metrics.micro.false_negative,
            critical_missing_label_count=critical_missing_label_count,
            critical_error_rate=_rounded_ratio(
                critical_missing_label_count,
                critical_gold_label_count,
            ),
            evidence_error_count=evidence_error_count,
            evidence_error_rate=_rounded_ratio(
                evidence_error_count,
                metrics.micro.true_positive,
            ),
            approved_assignment_count=approved_assignment_count,
        )
        return DomainLabelGoldEvaluationReport(
            generated_at=datetime.now(UTC),
            dataset_version=dataset.version,
            dataset_name=dataset.name,
            selection_strategy=dataset.selection_strategy,
            source_key=dataset.source.source_key,
            poem_source_key=dataset.poem_source_key,
            metrics=metrics,
            coverage=coverage,
            quality=quality,
            records=record_reports,
        )

    async def _resolve_record(
        self,
        dataset: DomainLabelGoldDataset,
        record: DomainLabelGoldRecord,
    ) -> tuple[Poem, PoemVersion, PoemSource]:
        row = (
            await self.session.execute(
                select(Poem, PoemVersion, PoemSource)
                .join(PoemVersion, PoemVersion.poem_id == Poem.id)
                .join(PoemSource, PoemSource.poem_id == Poem.id)
                .where(
                    PoemSource.source_key == dataset.poem_source_key,
                    PoemSource.external_id == record.external_id,
                    PoemVersion.version_no == Poem.version_no,
                    Poem.status == PoemStatus.PUBLISHED.value,
                    Poem.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise ValueError(
                "金标准作品无法解析为已发布当前版本: "
                f"external_id={record.external_id}"
            )
        poem, version, source = row
        if source.content_hash != record.expected_source_content_hash:
            raise ValueError(
                "金标准来源内容哈希不一致，必须先复核版本: "
                f"external_id={record.external_id}"
            )
        return poem, version, source

    @staticmethod
    def _build_active_name_index(
        labels: list[DomainLabel],
        aliases: list[DomainLabelAlias],
    ) -> dict[tuple[str, str], set[int]]:
        active_ids = {
            label.id
            for label in labels
            if label.status == DomainLabelStatus.ACTIVE.value
        }
        index: dict[tuple[str, str], set[int]] = defaultdict(set)
        labels_by_id = {label.id: label for label in labels}
        for label in labels:
            if label.id not in active_ids:
                continue
            index[(label.dimension, label.normalized_name)].add(label.id)
        for alias in aliases:
            label = labels_by_id.get(alias.domain_label_id)
            if label is None or label.id not in active_ids:
                continue
            index[(label.dimension, alias.normalized_alias)].add(label.id)
        return index

    @staticmethod
    def _resolve_gold_labels(
        record: DomainLabelGoldRecord,
        active_label_ids_by_name: dict[tuple[str, str], set[int]],
        labels_by_id: dict[int, DomainLabel],
    ) -> dict[int, DomainLabelGoldLabel]:
        resolved: dict[int, DomainLabelGoldLabel] = {}
        for label in record.labels:
            candidates = active_label_ids_by_name.get(label.lookup_key, set())
            if not candidates:
                raise ValueError(
                    "金标准标签不存在或未启用: "
                    f"external_id={record.external_id} "
                    f"dimension={label.dimension.value} name={label.name}"
                )
            if len(candidates) > 1:
                names = ", ".join(
                    labels_by_id[label_id].canonical_name
                    for label_id in sorted(candidates)
                )
                raise ValueError(
                    "金标准标签存在歧义: "
                    f"external_id={record.external_id} name={label.name} "
                    f"candidates={names}"
                )
            label_id = next(iter(candidates))
            if label_id in resolved:
                raise ValueError(
                    "金标准别名解析后指向重复标签: "
                    f"external_id={record.external_id} "
                    f"label={labels_by_id[label_id].canonical_name}"
                )
            resolved[label_id] = label
        return resolved

    async def _load_predicted_labels(
        self,
        version_id: int,
        labels_by_id: dict[int, DomainLabel],
    ) -> tuple[dict[int, PoemVersionDomainLabel], list[PoemVersionDomainLabel]]:
        rows = await self.session.execute(
            select(PoemVersionDomainLabel)
            .where(
                PoemVersionDomainLabel.poem_version_id == version_id,
                PoemVersionDomainLabel.review_status
                == DomainLabelReviewStatus.APPROVED.value,
            )
            .order_by(
                PoemVersionDomainLabel.generation_method.asc(),
                PoemVersionDomainLabel.id.asc(),
            )
        )
        assignments = list(rows.scalars().all())
        selected: dict[int, PoemVersionDomainLabel] = {}
        for assignment in assignments:
            label = labels_by_id.get(assignment.domain_label_id)
            if label is None:
                continue
            resolved_label = _resolve_online_label(label, labels_by_id)
            if resolved_label is None:
                continue
            current = selected.get(resolved_label.id)
            if current is None or _assignment_rank(assignment) < _assignment_rank(current):
                selected[resolved_label.id] = assignment
        return selected, assignments

    def _compare_record(
        self,
        *,
        record: DomainLabelGoldRecord,
        poem: Poem,
        version: PoemVersion,
        gold_by_label_id: dict[int, DomainLabelGoldLabel],
        predicted: dict[int, PoemVersionDomainLabel],
        labels_by_id: dict[int, DomainLabel],
    ) -> DomainLabelGoldRecordReport:
        missing_labels: list[DomainLabelGoldMissingLabel] = []
        extra_labels: list[DomainLabelGoldExtraLabel] = []
        evidence_issues: list[DomainLabelGoldEvidenceIssue] = []
        self._validate_evidence_bounds(record, version)

        for label_id in sorted(set(gold_by_label_id) - set(predicted)):
            gold_label = gold_by_label_id[label_id]
            missing_labels.append(
                DomainLabelGoldMissingLabel(
                    dimension=gold_label.dimension.value,
                    canonical_name=gold_label.name,
                    severity=gold_label.severity,
                    expected_evidence=gold_label.evidence,
                )
            )

        for label_id in sorted(set(predicted) - set(gold_by_label_id)):
            assignment = predicted[label_id]
            label = labels_by_id[assignment.domain_label_id]
            extra_labels.append(
                DomainLabelGoldExtraLabel(
                    dimension=label.dimension,
                    label_id=label.id,
                    canonical_name=label.canonical_name,
                    generation_method=assignment.generation_method,
                    evidence_text=assignment.evidence_text,
                    line_start=assignment.line_start,
                    line_end=assignment.line_end,
                )
            )

        for label_id in sorted(set(gold_by_label_id) & set(predicted)):
            gold_label = gold_by_label_id[label_id]
            assignment = predicted[label_id]
            reason = _evidence_mismatch_reason(assignment, gold_label)
            if reason is not None:
                evidence_issues.append(
                    DomainLabelGoldEvidenceIssue(
                        dimension=gold_label.dimension.value,
                        canonical_name=gold_label.name,
                        severity=gold_label.severity,
                        expected_evidence=gold_label.evidence,
                        observed_evidence_text=assignment.evidence_text,
                        observed_line_start=assignment.line_start,
                        observed_line_end=assignment.line_end,
                        reason=reason,
                    )
                )

        return DomainLabelGoldRecordReport(
            external_id=record.external_id,
            poem_id=poem.id,
            version_id=version.id,
            title=record.title,
            author=record.author,
            gold_label_count=len(gold_by_label_id),
            predicted_label_count=len(predicted),
            missing_labels=missing_labels,
            extra_labels=extra_labels,
            evidence_issues=evidence_issues,
            critical_missing_label_count=sum(
                item.severity.value == "critical" for item in missing_labels
            ),
        )

    @staticmethod
    def _validate_evidence_bounds(
        record: DomainLabelGoldRecord,
        version: PoemVersion,
    ) -> None:
        content = version.snapshot.get("content") if version.snapshot else None
        if not isinstance(content, str) or not content:
            raise ValueError(
                "金标准当前版本缺少正文，无法校验证据行号: "
                f"external_id={record.external_id}"
            )
        line_count = len(content.splitlines())
        for label in record.labels:
            for evidence in label.evidence:
                if evidence.line_end > line_count:
                    raise ValueError(
                        "金标准证据行号超出当前版本正文范围: "
                        f"external_id={record.external_id} "
                        f"dimension={label.dimension.value} name={label.name} "
                        f"line_end={evidence.line_end} line_count={line_count}"
                    )

    @staticmethod
    def _build_metrics(
        gold_by_dimension: dict[str, set[tuple[str, int]]],
        predicted_by_dimension: dict[str, set[tuple[str, int]]],
    ) -> DomainLabelMetricSummary:
        dimension_metrics: dict[str, DomainLabelDimensionMetric] = {}
        total_tp = total_fp = total_fn = 0
        macro_precision: list[float] = []
        macro_recall: list[float] = []
        macro_f1: list[float] = []
        for dimension in DIMENSIONS:
            gold = gold_by_dimension.get(dimension, set())
            predicted = predicted_by_dimension.get(dimension, set())
            values = _metric_values(gold, predicted)
            dimension_metrics[dimension] = DomainLabelDimensionMetric(
                dimension=dimension,
                **values.model_dump(),
            )
            total_tp += values.true_positive
            total_fp += values.false_positive
            total_fn += values.false_negative
            if values.gold_count:
                macro_precision.append(values.precision)
                macro_recall.append(values.recall)
                macro_f1.append(values.f1)

        micro = _metric_from_counts(total_tp, total_fp, total_fn)
        macro_count = len(macro_precision)
        macro = DomainLabelMetricValues(
            true_positive=total_tp,
            false_positive=total_fp,
            false_negative=total_fn,
            gold_count=sum(item.gold_count for item in dimension_metrics.values()),
            predicted_count=sum(
                item.predicted_count for item in dimension_metrics.values()
            ),
            precision=(
                round(sum(macro_precision) / macro_count, 6)
                if macro_count
                else 0.0
            ),
            recall=(
                round(sum(macro_recall) / macro_count, 6)
                if macro_count
                else 0.0
            ),
            f1=round(sum(macro_f1) / macro_count, 6) if macro_count else 0.0,
        )
        return DomainLabelMetricSummary(
            micro=micro,
            macro=macro,
            macro_dimension_count=macro_count,
            dimensions=dimension_metrics,
        )


def format_domain_label_gold_report(report: DomainLabelGoldEvaluationReport) -> str:
    lines = [
        f"generated_at={report.generated_at.isoformat()}",
        (
            f"dataset={report.dataset_version} source_key={report.source_key} "
            f"records={report.coverage.gold_record_count}"
        ),
        (
            "metrics "
            f"micro_p={report.metrics.micro.precision:.6f} "
            f"micro_r={report.metrics.micro.recall:.6f} "
            f"micro_f1={report.metrics.micro.f1:.6f} "
            f"macro_p={report.metrics.macro.precision:.6f} "
            f"macro_r={report.metrics.macro.recall:.6f} "
            f"macro_f1={report.metrics.macro.f1:.6f} "
            f"macro_dimensions={report.metrics.macro_dimension_count}"
        ),
        (
            "quality "
            f"tp={report.quality.true_positive} "
            f"fp={report.quality.false_positive} "
            f"fn={report.quality.false_negative} "
            f"critical_missing={report.quality.critical_missing_label_count} "
            f"critical_rate={report.quality.critical_error_rate:.6f} "
            f"evidence_errors={report.quality.evidence_error_count} "
            f"evidence_error_rate={report.quality.evidence_error_rate:.6f}"
        ),
        (
            "coverage "
            f"gold_scope={report.coverage.gold_scope_coverage_rate:.6f} "
            f"predicted_records={report.coverage.records_with_predictions} "
            f"macro_plan={report.coverage.macro_plan_coverage_rate:.6f} "
            f"plan_gaps={report.coverage.total_plan_gap_count}"
        ),
        "dimensions:",
    ]
    for dimension in DIMENSIONS:
        metric = report.metrics.dimensions[dimension]
        coverage = report.coverage.dimensions[dimension]
        lines.append(
            f"- {dimension}: p={metric.precision:.6f} r={metric.recall:.6f} "
            f"f1={metric.f1:.6f} tp={metric.true_positive} fp={metric.false_positive} "
            f"fn={metric.false_negative} target_records="
            f"{coverage.target_record_count} gold_records={coverage.gold_record_count} "
            f"predicted_records={coverage.predicted_record_count} "
            f"plan_coverage={coverage.plan_coverage_rate:.6f} "
            f"plan_gap={coverage.plan_gap_count}"
        )
    return "\n".join(lines)


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


def _assignment_rank(assignment: PoemVersionDomainLabel) -> tuple[int, int]:
    return _ASSIGNMENT_PRIORITY.get(assignment.generation_method, 99), assignment.id


def _evidence_mismatch_reason(
    assignment: PoemVersionDomainLabel,
    gold_label: DomainLabelGoldLabel,
) -> str | None:
    if not assignment.evidence_text and (
        assignment.line_start is None or assignment.line_end is None
    ):
        return "已批准标签缺少证据文本和行号"
    if assignment.line_start is None or assignment.line_end is None:
        return "已批准标签缺少完整行号范围"
    if any(
        _line_ranges_overlap(
            assignment.line_start,
            assignment.line_end,
            evidence.line_start,
            evidence.line_end,
        )
        for evidence in gold_label.evidence
    ):
        return None
    return "已批准标签行号未命中任何金标准证据范围"


def _line_ranges_overlap(
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
) -> bool:
    return max(left_start, right_start) <= min(left_end, right_end)


def _metric_values(
    gold: set[tuple[str, int]],
    predicted: set[tuple[str, int]],
) -> DomainLabelMetricValues:
    return _metric_from_counts(
        true_positive=len(gold & predicted),
        false_positive=len(predicted - gold),
        false_negative=len(gold - predicted),
        gold_count=len(gold),
        predicted_count=len(predicted),
    )


def _metric_from_counts(
    true_positive: int,
    false_positive: int,
    false_negative: int,
    *,
    gold_count: int | None = None,
    predicted_count: int | None = None,
) -> DomainLabelMetricValues:
    effective_gold_count = (
        true_positive + false_negative if gold_count is None else gold_count
    )
    effective_predicted_count = (
        true_positive + false_positive if predicted_count is None else predicted_count
    )
    precision = _rounded_ratio(true_positive, effective_predicted_count)
    recall = _rounded_ratio(true_positive, effective_gold_count)
    f1 = (
        round(2 * precision * recall / (precision + recall), 6)
        if precision + recall
        else 0.0
    )
    return DomainLabelMetricValues(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        gold_count=effective_gold_count,
        predicted_count=effective_predicted_count,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _rounded_ratio(
    numerator: int,
    denominator: int,
    *,
    empty: float = 0.0,
) -> float:
    if denominator == 0:
        return empty
    return round(numerator / denominator, 6)
