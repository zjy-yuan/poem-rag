from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_JUDGE_REPORT = (
    PROJECT_ROOT
    / "data"
    / "eval"
    / "reports"
    / "generation_holdout_1000_v3_judge.json"
)


def _load_judge_report(path: Path):
    from app.schemas.generation_judge import GenerationJudgeReport

    return GenerationJudgeReport.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _selected_case_ids(
    report,
    *,
    scope: str,
) -> set[str]:
    judged_case_ids = {
        result.case_id
        for result in report.results
        if result.judge_status == "judged"
        and result.assessment is not None
    }
    if scope == "all":
        return judged_case_ids
    return judged_case_ids.intersection(report.judge_failure_case_ids)


def _export_template(
    *,
    report_path: Path,
    template_path: Path,
    review_path: Path | None,
    scope: str,
) -> None:
    from app.evaluation.generation_judge import export_blind_review_markdown
    from app.evaluation.generation_judge_calibration import (
        export_calibration_template,
    )

    report = _load_judge_report(report_path)
    case_ids = _selected_case_ids(report, scope=scope)
    if not case_ids:
        raise ValueError("没有符合当前范围且可校准的 judge 成功样本")
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(
        export_calibration_template(report, case_ids=case_ids),
        encoding="utf-8",
    )
    print(f"template={template_path}")
    print(f"scope={scope} cases={len(case_ids)}")
    if review_path is not None:
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(
            export_blind_review_markdown(report, case_ids=case_ids),
            encoding="utf-8",
        )
        print(f"review_sheet={review_path}")


def _calibrate(
    *,
    report_path: Path,
    review_input: Path,
    json_output: Path | None,
    summary_output: Path | None,
) -> None:
    from app.evaluation.generation_judge_calibration import (
        calibrate_generation_judge,
        format_calibration_summary,
        load_human_reviews,
    )

    report = _load_judge_report(report_path)
    human_reviews = load_human_reviews(
        review_input.read_text(encoding="utf-8-sig")
    )
    calibration = calibrate_generation_judge(report, human_reviews)
    summary = format_calibration_summary(calibration)
    print(summary)
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            calibration.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(f"\njson_report={json_output}")
    if summary_output is not None:
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary_output.write_text(
            summary.rstrip() + "\n",
            encoding="utf-8",
        )
        print(f"summary_report={summary_output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Export or score a human-review CSV for an existing generation "
            "judge report. This command never calls a model or changes the "
            "online RAG path."
        )
    )
    parser.add_argument(
        "--judge-report",
        type=Path,
        default=DEFAULT_JUDGE_REPORT,
        help=f"Source judge report. Default: {DEFAULT_JUDGE_REPORT}",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--export-template",
        type=Path,
        help="Write a blank human-review CSV and exit.",
    )
    mode.add_argument(
        "--review-input",
        type=Path,
        help="Read a completed human-review CSV and calculate calibration.",
    )
    parser.add_argument(
        "--scope",
        choices=("flagged", "all"),
        default="flagged",
        help="Template scope. flagged only exports judge failure candidates.",
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=None,
        help="Optional focused blind-review Markdown for template mode.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional calibration JSON report.",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=None,
        help="Optional calibration summary Markdown.",
    )
    args = parser.parse_args()
    if args.review_input is not None and args.review_output is not None:
        parser.error("--review-output 只能与 --export-template 一起使用")
    if args.review_input is not None and args.scope != "flagged":
        parser.error("--scope 只能用于 --export-template")

    try:
        if args.export_template is not None:
            _export_template(
                report_path=args.judge_report,
                template_path=args.export_template,
                review_path=args.review_output,
                scope=args.scope,
            )
        else:
            _calibrate(
                report_path=args.judge_report,
                review_input=args.review_input,
                json_output=args.json_output,
                summary_output=args.summary_output,
            )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
