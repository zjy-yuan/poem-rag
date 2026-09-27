from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_ROOT.parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if TYPE_CHECKING:
    from app.schemas.domain_label_sampling import DomainLabelSamplingManifest

DEFAULT_SOURCE_KEY = "aopao-chinese-gushiwen"
DEFAULT_COUNT = 24
DEFAULT_SEED = "domain-label-gold-v2"
DEFAULT_EXCLUDE = PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "eval" / "domain_label_gold_v2_sampling.json"


async def prepare_sampling_manifest(
    *,
    source_key: str,
    count: int,
    seed: str,
    excluded_external_ids: set[str],
) -> DomainLabelSamplingManifest:
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.evaluation.domain_label_sampling import DomainLabelSamplingPreparer

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await DomainLabelSamplingPreparer(session).prepare(
                source_key=source_key,
                count=count,
                seed=seed,
                excluded_external_ids=excluded_external_ids,
            )
    finally:
        await engine.dispose()


def _load_excluded_external_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"排除文件缺少 records 列表: {path}")
    external_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"排除文件第 {index} 条记录不是对象: {path}")
        external_id = record.get("external_id")
        if not isinstance(external_id, str) or not external_id.strip():
            raise ValueError(f"排除文件第 {index} 条记录缺少 external_id: {path}")
        external_ids.add(external_id.strip())
    return external_ids


def _format_summary(manifest: DomainLabelSamplingManifest) -> str:
    counts = Counter(record.stratum.value for record in manifest.records)
    lines = [
        (
            f"source_key={manifest.source_key} requested={manifest.requested_count} "
            f"sampled={manifest.sampled_count} seed={manifest.random_seed}"
        ),
        f"excluded_external_ids={len(manifest.excluded_external_ids)}",
        "strata:",
    ]
    lines.extend(
        f"- {stratum}: {counts.get(stratum.value, 0)}"
        for stratum in manifest.records[0].stratum.__class__
    )
    lines.append("records:")
    lines.extend(
        f"- {record.external_id} | {record.dynasty} | {record.author} | "
        f"{record.title} | lines={record.line_count} | stratum={record.stratum.value}"
        for record in manifest.records
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a deterministic, label-blind manual sampling manifest from "
            "published poems. The command reads MySQL and writes only the manifest file."
        )
    )
    parser.add_argument("--source-key", default=DEFAULT_SOURCE_KEY)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument(
        "--exclude",
        type=Path,
        default=DEFAULT_EXCLUDE,
        help="Frozen gold dataset whose external_id values must be excluded.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.count < 4:
        parser.error("--count 必须大于等于 4")

    try:
        excluded_external_ids = _load_excluded_external_ids(args.exclude)
        manifest = asyncio.run(
            prepare_sampling_manifest(
                source_key=args.source_key,
                count=args.count,
                seed=args.seed,
                excluded_external_ids=excluded_external_ids,
            )
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(2, f"sampling preparation failed: {exc}\n")

    print(_format_summary(manifest))
    print(f"\noutput={args.output}")


if __name__ == "__main__":
    main()
