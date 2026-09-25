from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def main() -> None:
    from app.services.chinese_gushiwen_conversion import (
        convert_chinese_gushiwen_file,
        convert_chinese_gushiwen_files,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Convert fixed aopao/chinese-gushiwen NDJSON shards into the "
            "Poem RAG corpus import format."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        type=Path,
        help="Path to a single guwen shard.",
    )
    source.add_argument(
        "--input-dir",
        type=Path,
        help="Directory containing guwen*.json shards.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path for the converted CorpusImportDataset JSON.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path for the conversion manifest JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Number of stratified records to select.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish imported records by default instead of creating drafts.",
    )
    args = parser.parse_args()
    limit = args.limit if args.limit is not None else (1000 if args.input_dir else 100)
    output = args.output or (
        PROJECT_ROOT
        / f"data/import/generated/chinese-gushiwen-{limit}-v2.json"
    )
    manifest = args.manifest or (
        PROJECT_ROOT
        / f"data/import/reports/chinese-gushiwen-{limit}-v2.manifest.json"
    )

    try:
        if args.input_dir is not None:
            input_paths = sorted(
                args.input_dir.glob("guwen*.json"),
                key=_natural_sort_key,
            )
            if not input_paths:
                raise ValueError(f"no guwen*.json shards found in {args.input_dir}")
            result = convert_chinese_gushiwen_files(
                input_paths,
                limit=limit,
                publish=args.publish,
            )
        else:
            result = convert_chinese_gushiwen_file(
                args.input,
                limit=limit,
                publish=args.publish,
            )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"conversion failed: {exc}\n")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        result.dataset.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(result.manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"dataset_version={result.dataset.version} "
        f"records={len(result.dataset.records)} "
        f"source_key={result.dataset.source.source_key}"
    )
    print(f"output={output}")
    print(f"manifest={manifest}")


def _natural_sort_key(path: Path) -> tuple[str, int, str]:
    match = re.search(r"(\d+)", path.stem)
    if match is None:
        return path.stem, 0, path.name
    return path.stem[: match.start()], int(match.group(1)), path.name


if __name__ == "__main__":
    main()
