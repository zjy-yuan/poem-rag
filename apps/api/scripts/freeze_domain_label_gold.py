from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_MANIFEST = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"
)
DEFAULT_ANNOTATION = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_annotation.json"
)
DEFAULT_REVIEW = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_review.json"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2.json"


def _ensure_new_file(path: Path) -> None:
    if path.exists():
        raise FileExistsError(
            "输出文件已存在，拒绝覆盖可能包含人工结论的文件: " f"{path}"
        )


def _write_new_file(path: Path, content: str) -> None:
    _ensure_new_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze the independently reviewed domain-label v2 annotations "
            "into a gold dataset. severity comes from the critical rule table, "
            "never from per-record judgment. This command never calls a "
            "model, reads MySQL, or changes online label visibility."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--annotation", type=Path, default=DEFAULT_ANNOTATION)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=None,
        help="Optional path for the critical-rule audit report JSON.",
    )
    args = parser.parse_args()

    from app.evaluation.domain_label_gold_freeze import (
        CRITICAL_PATTERNS,
        DomainLabelGoldFreezer,
        format_domain_label_gold_freeze_summary,
    )
    from app.schemas.domain_label_gold_freeze import (
        DomainLabelCriticalAuditReport,
    )

    try:
        freezer = DomainLabelGoldFreezer.from_path(
            manifest_path=args.manifest,
            annotation_path=args.annotation,
            review_path=args.review,
        )
        dataset, summary = freezer.freeze()
        _write_new_file(args.output, dataset.model_dump_json(indent=2) + "\n")
        if args.audit_output is not None:
            report = DomainLabelCriticalAuditReport(
                version=f"{summary.version}-critical-audit",
                generated_at=datetime.now(UTC),
                dataset_version=summary.version,
                critical_rule_count=len(CRITICAL_PATTERNS),
                audited_critical_label_count=summary.critical_label_count,
                issue_count=summary.audit_issue_count,
                issues=summary.audit_issues,
            )
            _write_new_file(
                args.audit_output,
                report.model_dump_json(indent=2) + "\n",
            )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"domain-label gold freeze failed: {exc}\n")

    print(format_domain_label_gold_freeze_summary(summary))
    print(f"\ngold_dataset={args.output}")
    if args.audit_output is not None:
        print(f"critical_audit_report={args.audit_output}")


if __name__ == "__main__":
    main()
