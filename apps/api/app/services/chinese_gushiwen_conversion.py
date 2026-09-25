from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.text import normalize_content, normalize_lookup
from app.models.annotation import AnnotationType
from app.schemas.corpus_import import (
    CorpusImportAnnotation,
    CorpusImportDataset,
    CorpusImportDefaults,
    CorpusImportRecord,
    CorpusImportSource,
)

SOURCE_KEY = "aopao-chinese-gushiwen"
SOURCE_NAME = "aopao/chinese-gushiwen"
SOURCE_COMMIT = "c2345d0abf2404b8b3601e4afc2e8fd12f90d6c8"
SOURCE_REPOSITORY_URL = f"https://github.com/aopao/chinese-gushiwen/tree/{SOURCE_COMMIT}"
SOURCE_SHARD_DIRECTORY = "guwen"
SOURCE_FILE_URL = (
    "https://raw.githubusercontent.com/aopao/chinese-gushiwen/"
    f"{SOURCE_COMMIT}/guwen/guwen0-1000.json"
)
SOURCE_FILE_NAME = "guwen/guwen0-1000.json"
DATASET_VERSION = "chinese-gushiwen-c2345d0-guwen-10000-v2"
LICENSE_NOTE = (
    "aopao/chinese-gushiwen 未声明 LICENSE，README 说明数据来自网络、仅供交流学习；"
    "当前仅用于学习和非公开技术展示，版权归原权利人，公开部署前需复核授权并提供"
    "来源与移除入口。"
)

POETRY_TYPE_MARKERS = frozenset(
    {
        "唐诗三百首",
        "古诗三百首",
        "宋词三百首",
        "宋词精选",
        "诗经",
        "乐府",
        "古诗十九首",
        "元曲精选",
        "楚辞",
    }
)

POETRY_EDUCATION_TYPE_MARKERS = frozenset(
    {
        "小学古诗",
        "初中古诗",
        "高中古诗",
        "早教古诗100首",
        "古诗里的十二个月",
    }
)

PROSE_TYPE_MARKERS = frozenset(
    {
        "初中文言文",
        "高中文言文",
        "小学文言文",
        "古文观止",
        "辞赋精选",
    }
)

POETRY_LABEL_PARTS = ("古诗", "宋词")
POETRY_LABELS = frozenset({"诗", "词", "唐诗", "宋诗"})
MAX_POEMLIKE_CONTENT_LENGTH = 600

DYNASTY_NAME_MAP = {
    "唐代": "唐",
    "宋代": "宋",
    "元代": "元",
    "明代": "明",
    "清代": "清",
    "隋代": "隋",
    "金朝": "金",
    "两汉": "汉",
}

_LENGTH_BUCKETS = ("short", "medium", "long")


@dataclass(frozen=True)
class ConversionResult:
    dataset: CorpusImportDataset
    manifest: dict[str, Any]


@dataclass(frozen=True)
class _Candidate:
    source_index: int
    record: CorpusImportRecord
    dynasty: str
    length_bucket: str


@dataclass(frozen=True)
class _SourceRecord:
    raw: Mapping[str, Any]
    file_name: str
    file_url: str
    source_line: int


def parse_ndjson_text(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"line {line_number}: invalid JSON: {exc.msg}",
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number}: expected a JSON object")
        records.append(value)
    if not records:
        raise ValueError("input contains no NDJSON records")
    return records


def convert_chinese_gushiwen_file(
    input_path: Path,
    *,
    limit: int = 100,
    publish: bool = False,
) -> ConversionResult:
    return convert_chinese_gushiwen_files(
        [input_path],
        limit=limit,
        publish=publish,
    )


def convert_chinese_gushiwen_files(
    input_paths: Sequence[Path],
    *,
    limit: int = 100,
    publish: bool = False,
) -> ConversionResult:
    if not input_paths:
        raise ValueError("at least one input file is required")

    source_records: list[_SourceRecord] = []
    source_files: list[dict[str, Any]] = []
    seen_file_names: set[str] = set()
    for input_path in input_paths:
        raw_bytes = input_path.read_bytes()
        records = parse_ndjson_text(raw_bytes.decode("utf-8"))
        file_name = _source_file_name(input_path)
        if file_name in seen_file_names:
            raise ValueError(f"duplicate source file name: {file_name}")
        seen_file_names.add(file_name)
        file_url = _source_file_url(file_name)
        source_files.append(
            {
                "file_name": file_name,
                "file_url": file_url,
                "input_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "input_records": len(records),
            }
        )
        source_records.extend(
            _SourceRecord(
                raw=record,
                file_name=file_name,
                file_url=file_url,
                source_line=line_number,
            )
            for line_number, record in enumerate(records, start=1)
        )

    return _convert_chinese_gushiwen(
        source_records,
        limit=limit,
        publish=publish,
        source_files=source_files,
    )


def convert_chinese_gushiwen(
    records: Sequence[Mapping[str, Any]],
    *,
    limit: int = 100,
    publish: bool = False,
    input_sha256: str | None = None,
) -> ConversionResult:
    source_records = [
        _SourceRecord(
            raw=record,
            file_name=SOURCE_FILE_NAME,
            file_url=SOURCE_FILE_URL,
            source_line=line_number,
        )
        for line_number, record in enumerate(records, start=1)
    ]
    return _convert_chinese_gushiwen(
        source_records,
        limit=limit,
        publish=publish,
        source_files=[
            {
                "file_name": SOURCE_FILE_NAME,
                "file_url": SOURCE_FILE_URL,
                "input_sha256": input_sha256,
                "input_records": len(records),
            }
        ],
    )


def _convert_chinese_gushiwen(
    source_records: Sequence[_SourceRecord],
    *,
    limit: int,
    publish: bool,
    source_files: Sequence[Mapping[str, Any]],
) -> ConversionResult:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    source_annotation_counts = _annotation_completeness(
        [item.raw for item in source_records],
    )
    candidates: list[_Candidate] = []
    seen_keys: set[tuple[str, str, str]] = set()
    excluded_non_poetry = 0
    excluded_explicit_prose = 0
    excluded_low_confidence = 0
    duplicate_count = 0

    for source_index, source_record in enumerate(source_records, start=1):
        raw = source_record.raw
        types = _source_types(raw)
        if not _is_poetry_candidate(raw, types):
            excluded_non_poetry += 1
            if PROSE_TYPE_MARKERS.intersection(types):
                excluded_explicit_prose += 1
            else:
                excluded_low_confidence += 1
            continue

        record = _build_record(source_record, types=types)
        duplicate_key = (
            normalize_lookup(record.author_name or ""),
            normalize_lookup(record.title),
            normalize_content(record.content),
        )
        if duplicate_key in seen_keys:
            duplicate_count += 1
            continue
        seen_keys.add(duplicate_key)

        candidates.append(
            _Candidate(
                source_index=source_index,
                record=record,
                dynasty=record.dynasty_name or "未知",
                length_bucket=_length_bucket(record.content),
            )
        )

    if len(candidates) < limit:
        raise ValueError(
            f"eligible unique poetry records={len(candidates)} are fewer than limit={limit}",
        )

    selected, dynasty_quotas = _select_stratified(candidates, limit=limit)
    selected_records = [item.record for item in selected]
    dataset = CorpusImportDataset(
        version=DATASET_VERSION,
        source=CorpusImportSource(
            source_key=SOURCE_KEY,
            source_type="file",
            source_name=SOURCE_NAME,
            source_url=SOURCE_REPOSITORY_URL,
            license_note=LICENSE_NOTE,
        ),
        defaults=CorpusImportDefaults(publish=publish),
        records=selected_records,
    )

    selected_dynasties = Counter(item.dynasty for item in selected)
    selected_lengths = Counter(item.length_bucket for item in selected)
    manifest: dict[str, Any] = {
        "manifest_version": "chinese-gushiwen-conversion-v2",
        "dataset_version": DATASET_VERSION,
        "source": {
            "source_key": SOURCE_KEY,
            "source_name": SOURCE_NAME,
            "repository_url": SOURCE_REPOSITORY_URL,
            "commit": SOURCE_COMMIT,
            "file_name": None,
            "file_url": None,
            "input_sha256": None,
            "license_note": LICENSE_NOTE,
        },
        "selection": {
            "method": "dynasty_and_content_length_stratified_v1",
            "limit": limit,
            "publish": publish,
            "input_records": len(source_records),
            "excluded_non_poetry": excluded_non_poetry,
            "excluded_explicit_prose": excluded_explicit_prose,
            "excluded_low_confidence": excluded_low_confidence,
            "duplicate_candidates": duplicate_count,
            "eligible_unique_records": len(candidates),
            "selected_records": len(selected_records),
            "poetry_type_markers": sorted(POETRY_TYPE_MARKERS),
            "poetry_education_type_markers": sorted(
                POETRY_EDUCATION_TYPE_MARKERS,
            ),
            "prose_type_markers": sorted(PROSE_TYPE_MARKERS),
            "dynasty_quotas": dict(sorted(dynasty_quotas.items())),
        },
        "distributions": {
            "dynasties": dict(sorted(selected_dynasties.items())),
            "content_lengths": {
                bucket: selected_lengths.get(bucket, 0) for bucket in _LENGTH_BUCKETS
            },
        },
        "annotation_completeness": {
            "source": source_annotation_counts,
            "selected": {
                "remark": sum(
                    _has_annotation(item.record, AnnotationType.NOTE)
                    for item in selected
                ),
                "translation": sum(
                    _has_annotation(item.record, AnnotationType.TRANSLATION)
                    for item in selected
                ),
                "shangxi": sum(
                    _has_annotation(item.record, AnnotationType.APPRECIATION)
                    for item in selected
                ),
            },
        },
    }
    single_source_file = source_files[0] if len(source_files) == 1 else None
    manifest["source"].update(
        {
            "file_count": len(source_files),
            "file_name": (
                single_source_file["file_name"]
                if single_source_file is not None
                else None
            ),
            "file_url": (
                single_source_file["file_url"]
                if single_source_file is not None
                else None
            ),
            "input_sha256": (
                single_source_file["input_sha256"]
                if single_source_file is not None
                else None
            ),
            "files": list(source_files),
        },
    )
    return ConversionResult(dataset=dataset, manifest=manifest)


def _build_record(
    source_record: _SourceRecord,
    *,
    types: tuple[str, ...],
) -> CorpusImportRecord:
    raw = source_record.raw
    source_index = source_record.source_line
    external_id = _source_id(raw, source_index)
    source_dynasty = _required_text(raw, "dynasty", source_index)
    source_writer = _required_text(raw, "writer", source_index)
    audio_url = _optional_text(raw.get("audioUrl"))
    raw_payload: dict[str, Any] = {
        "source_file": source_record.file_name,
        "source_line": source_index,
        "source_id": external_id,
        "source_dynasty": source_dynasty,
        "source_writer": source_writer,
        "source_types": list(types),
    }
    if audio_url is not None:
        raw_payload["audio_url"] = audio_url

    return CorpusImportRecord(
        external_id=external_id,
        title=_required_text(raw, "title", source_index),
        content=_required_text(raw, "content", source_index),
        author_name=source_writer,
        dynasty_name=DYNASTY_NAME_MAP.get(source_dynasty, source_dynasty),
        summary=None,
        source_url=source_record.file_url,
        tags=list(types[:30]),
        annotations=_annotations(raw, source_index),
        raw_payload=raw_payload,
    )


def _annotations(
    raw: Mapping[str, Any],
    source_index: int,
) -> list[CorpusImportAnnotation]:
    annotations: list[CorpusImportAnnotation] = []
    for annotation_type, title, field_name in (
        (AnnotationType.NOTE, "注释", "remark"),
        (AnnotationType.TRANSLATION, "译文", "translation"),
        (AnnotationType.APPRECIATION, "赏析", "shangxi"),
    ):
        content = _optional_text(raw.get(field_name))
        if content is None:
            continue
        annotations.append(
            CorpusImportAnnotation(
                type=annotation_type,
                title=title,
                content=content,
            )
        )
    return annotations


def _select_stratified(
    candidates: list[_Candidate],
    *,
    limit: int,
) -> tuple[list[_Candidate], dict[str, int]]:
    pools: dict[str, dict[str, list[_Candidate]]] = {}
    for candidate in candidates:
        dynasty_pool = pools.setdefault(
            candidate.dynasty,
            {bucket: [] for bucket in _LENGTH_BUCKETS},
        )
        dynasty_pool[candidate.length_bucket].append(candidate)

    pool_sizes = {
        dynasty: sum(len(bucket) for bucket in dynasty_pool.values())
        for dynasty, dynasty_pool in pools.items()
    }
    quotas = _allocate_dynasty_quotas(pool_sizes, limit=limit)

    selected: list[_Candidate] = []
    dynasty_order = sorted(pool_sizes, key=lambda dynasty: (-pool_sizes[dynasty], dynasty))
    for dynasty in dynasty_order:
        dynasty_pool = pools[dynasty]
        bucket_order = [
            bucket for bucket in _LENGTH_BUCKETS if dynasty_pool[bucket]
        ]
        cursors = {bucket: 0 for bucket in bucket_order}
        selected_for_dynasty = 0
        while selected_for_dynasty < quotas[dynasty]:
            progressed = False
            for bucket in bucket_order:
                cursor = cursors[bucket]
                if cursor >= len(dynasty_pool[bucket]):
                    continue
                selected.append(dynasty_pool[bucket][cursor])
                cursors[bucket] = cursor + 1
                selected_for_dynasty += 1
                progressed = True
                if selected_for_dynasty >= quotas[dynasty]:
                    break
            if not progressed:
                raise ValueError(f"unable to fill dynasty quota for {dynasty}")

    selected.sort(key=lambda item: item.source_index)
    return selected, quotas


def _allocate_dynasty_quotas(
    pool_sizes: Mapping[str, int],
    *,
    limit: int,
) -> dict[str, int]:
    if limit < len(pool_sizes):
        raise ValueError(
            f"limit={limit} cannot cover {len(pool_sizes)} dynasties with minimum quota 1",
        )

    ordered = sorted(pool_sizes, key=lambda dynasty: (-pool_sizes[dynasty], dynasty))
    order_index = {dynasty: index for index, dynasty in enumerate(ordered)}
    quotas = {dynasty: 1 for dynasty in ordered}
    total = sum(pool_sizes.values())
    ideal = {
        dynasty: limit * pool_sizes[dynasty] / total
        for dynasty in ordered
    }

    for _ in range(limit - len(ordered)):
        available = [
            dynasty
            for dynasty in ordered
            if quotas[dynasty] < pool_sizes[dynasty]
        ]
        if not available:
            raise ValueError("eligible records cannot satisfy the requested limit")
        dynasty = max(
            available,
            key=lambda item: (
                ideal[item] - quotas[item],
                pool_sizes[item] - quotas[item],
                -order_index[item],
            ),
        )
        quotas[dynasty] += 1
    return quotas


def _source_types(raw: Mapping[str, Any]) -> tuple[str, ...]:
    value = raw.get("type")
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [item for item in value if isinstance(item, str)]
    else:
        values = []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        label = item.strip()
        lookup = normalize_lookup(label)
        if not label or lookup in seen:
            continue
        seen.add(lookup)
        normalized.append(label)
    return tuple(normalized)


def _is_poetry_candidate(
    raw: Mapping[str, Any],
    types: tuple[str, ...],
) -> bool:
    if PROSE_TYPE_MARKERS.intersection(types):
        return False
    if POETRY_TYPE_MARKERS.intersection(types):
        return True
    if POETRY_EDUCATION_TYPE_MARKERS.intersection(types):
        return True
    if any(
        label in POETRY_LABELS or any(part in label for part in POETRY_LABEL_PARTS)
        for label in types
    ):
        return True
    return _looks_like_poem(raw.get("content"), raw.get("title"))


def _looks_like_poem(content_value: Any, title_value: Any) -> bool:
    if not isinstance(content_value, str):
        return False
    content = content_value.strip()
    if (
        not content
        or len(content) > MAX_POEMLIKE_CONTENT_LENGTH
        or content.startswith(("　", " "))
    ):
        return False
    if isinstance(title_value, str) and "·" in title_value:
        return True

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    line_lengths = [len(line) for line in lines]
    if max(line_lengths) > 45:
        return False
    if sum(length <= 30 for length in line_lengths) / len(line_lengths) < 0.8:
        return False
    cjk_count = sum("\u4e00" <= character <= "\u9fff" for character in content)
    return cjk_count / len(content) >= 0.6


def _source_id(raw: Mapping[str, Any], source_index: int) -> str:
    value = raw.get("_id")
    if not isinstance(value, Mapping):
        raise ValueError(f"line {source_index}: missing _id object")
    oid = value.get("$oid")
    if not isinstance(oid, str) or not oid.strip():
        raise ValueError(f"line {source_index}: missing _id.$oid")
    return oid.strip()


def _required_text(
    raw: Mapping[str, Any],
    field_name: str,
    source_index: int,
) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"line {source_index}: missing {field_name}")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _length_bucket(content: str) -> str:
    length = len(content)
    if length < 80:
        return "short"
    if length <= 300:
        return "medium"
    return "long"


def _annotation_completeness(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    return {
        "remark": sum(_has_text(record.get("remark")) for record in records),
        "translation": sum(
            _has_text(record.get("translation")) for record in records
        ),
        "shangxi": sum(_has_text(record.get("shangxi")) for record in records),
    }


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_annotation(
    record: CorpusImportRecord,
    annotation_type: AnnotationType,
) -> bool:
    return any(item.type == annotation_type for item in record.annotations)


def _source_file_name(input_path: Path) -> str:
    if input_path.name == "chinese-gushiwen-guwen0-1000-c2345d0.json":
        return SOURCE_FILE_NAME
    return f"{SOURCE_SHARD_DIRECTORY}/{input_path.name}"


def _source_file_url(file_name: str) -> str:
    return (
        "https://raw.githubusercontent.com/aopao/chinese-gushiwen/"
        f"{SOURCE_COMMIT}/{file_name}"
    )
