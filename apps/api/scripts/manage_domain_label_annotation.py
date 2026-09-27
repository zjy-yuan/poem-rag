from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if TYPE_CHECKING:
    from app.schemas.domain_label_annotation import DomainLabelAnnotationDraft

DEFAULT_MANIFEST = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"
)
DEFAULT_ANNOTATION = (
    PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_annotation.json"
)
DEFAULT_REVIEW_OUTPUT = (
    PROJECT_ROOT / "docs" / "reviews" / "20260927-domain-label-v2-annotation.md"
)


def _ensure_new_file(path: Path) -> None:
    if path.exists():
        raise FileExistsError(
            "输出文件已存在，拒绝覆盖可能包含人工标注的文件: "
            f"{path}"
        )


def _write_new_file(path: Path, content: str) -> None:
    _ensure_new_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_annotation(path: Path) -> DomainLabelAnnotationDraft:
    from app.schemas.domain_label_annotation import DomainLabelAnnotationDraft

    return DomainLabelAnnotationDraft.model_validate_json(path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create or validate the offline workspace for the domain-label v2 "
            "independent manual annotation. This command never calls a model and "
            "never reads or writes MySQL."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--annotation", type=Path, default=DEFAULT_ANNOTATION)
    parser.add_argument(
        "--review-output",
        type=Path,
        default=DEFAULT_REVIEW_OUTPUT,
        help="Markdown review sheet written only with --init.",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--init",
        action="store_true",
        help="Create a blank annotation draft and line-numbered review sheet.",
    )
    action.add_argument(
        "--validate",
        action="store_true",
        help="Validate a completed or in-progress annotation draft.",
    )
    args = parser.parse_args()

    from app.evaluation.domain_label_annotation import (
        DomainLabelAnnotationWorkspace,
        format_annotation_validation_summary,
    )

    try:
        workspace = DomainLabelAnnotationWorkspace.from_path(args.manifest)
        if args.init:
            draft = workspace.create_draft()
            _ensure_new_file(args.annotation)
            _ensure_new_file(args.review_output)
            _write_new_file(
                args.annotation,
                draft.model_dump_json(indent=2) + "\n",
            )
            _write_new_file(
                args.review_output,
                workspace.export_review_markdown(),
            )
            summary = workspace.validate(draft)
            print(format_annotation_validation_summary(summary))
            print(f"\nannotation={args.annotation}")
            print(f"review_sheet={args.review_output}")
            return

        draft = _load_annotation(args.annotation)
        summary = workspace.validate(draft)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"domain-label annotation failed: {exc}\n")

    print(format_annotation_validation_summary(summary))


if __name__ == "__main__":
    main()
