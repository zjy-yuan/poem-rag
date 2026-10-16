from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if TYPE_CHECKING:
    from app.schemas.domain_label_review import DomainLabelGoldV2Review

DEFAULT_MANIFEST = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"
)
DEFAULT_ANNOTATION = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_annotation.json"
)
DEFAULT_REVIEW = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_review.json"
)
DEFAULT_MARKDOWN = (
    PROJECT_ROOT
    / "docs"
    / "reviews"
    / "20261016-domain-label-v2-independent-review.md"
)


def _ensure_new_file(path: Path) -> None:
    if path.exists():
        raise FileExistsError(
            "输出文件已存在，拒绝覆盖可能包含人工复核的文件: "
            f"{path}"
        )


def _write_new_file(path: Path, content: str) -> None:
    _ensure_new_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_review(path: Path) -> DomainLabelGoldV2Review:
    from app.schemas.domain_label_review import DomainLabelGoldV2Review

    return DomainLabelGoldV2Review.model_validate_json(path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create or validate the offline independent review workspace for "
            "domain-label v2. This command never calls a model, reads MySQL, "
            "or changes online label visibility."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--annotation", type=Path, default=DEFAULT_ANNOTATION)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument(
        "--review-output",
        type=Path,
        default=DEFAULT_MARKDOWN,
        help="Markdown review sheet written by --init.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--init",
        action="store_true",
        help="Create a blank review draft and a human-readable review sheet.",
    )
    action.add_argument(
        "--validate",
        action="store_true",
        help="Validate review coverage, decisions, corrections, and evidence.",
    )
    args = parser.parse_args()

    from app.evaluation.domain_label_review import (
        DomainLabelGoldV2ReviewWorkspace,
        format_domain_label_review_summary,
    )

    try:
        workspace = DomainLabelGoldV2ReviewWorkspace.from_path(
            manifest_path=args.manifest,
            annotation_path=args.annotation,
        )
        if args.init:
            review = workspace.create_review()
            _ensure_new_file(args.review)
            _ensure_new_file(args.review_output)
            _write_new_file(
                args.review,
                review.model_dump_json(indent=2) + "\n",
            )
            _write_new_file(
                args.review_output,
                workspace.export_review_markdown(review),
            )
            summary = workspace.validate(review)
            print(format_domain_label_review_summary(summary))
            print(f"\nreview={args.review}")
            print(f"review_sheet={args.review_output}")
            return

        review = _load_review(args.review)
        summary = workspace.validate(review)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"domain-label review failed: {exc}\n")

    print(format_domain_label_review_summary(summary))


if __name__ == "__main__":
    main()
