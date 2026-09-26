from __future__ import annotations

import json
from typing import Any

import pytest
from app.models.annotation import AnnotationType
from app.services.chinese_gushiwen_conversion import (
    convert_chinese_gushiwen,
    convert_chinese_gushiwen_files,
    parse_ndjson_text,
)


def _raw_record(
    external_id: str,
    *,
    title: str = "静夜思",
    dynasty: str = "唐代",
    writer: str = "李白",
    content: str = "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。",
    types: list[str] | None = None,
    remark: str | None = "床前：卧室的床前。",
    translation: str | None = "明亮的月光洒在床前。",
    shangxi: str | None = "明月与思乡构成主要意境。",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "_id": {"$oid": external_id},
        "title": title,
        "dynasty": dynasty,
        "writer": writer,
        "content": content,
        "type": types if types is not None else ["唐诗三百首", "思乡"],
        "remark": remark,
        "translation": translation,
        "shangxi": shangxi,
    }
    return record


def test_parse_ndjson_reports_invalid_line() -> None:
    with pytest.raises(ValueError, match="line 2"):
        parse_ndjson_text('{"title":"ok"}\nnot-json\n')


def test_conversion_maps_source_fields_and_annotations() -> None:
    result = convert_chinese_gushiwen(
        [_raw_record("poem-1")],
        limit=1,
        input_sha256="a" * 64,
    )

    record = result.dataset.records[0]
    assert record.external_id == "poem-1"
    assert record.title == "静夜思"
    assert record.author_name == "李白"
    assert record.dynasty_name == "唐"
    assert record.tags == ["唐诗三百首", "思乡"]
    assert [annotation.type for annotation in record.annotations] == [
        AnnotationType.NOTE,
        AnnotationType.TRANSLATION,
        AnnotationType.APPRECIATION,
    ]
    assert [annotation.title for annotation in record.annotations] == [
        "注释",
        "译文",
        "赏析",
    ]
    assert record.raw_payload is not None
    assert record.raw_payload["source_dynasty"] == "唐代"
    assert result.dataset.defaults.publish is False
    assert result.manifest["source"]["input_sha256"] == "a" * 64
    assert result.manifest["selection"]["selected_records"] == 1
    assert result.manifest["selection"]["publish"] is False


def test_conversion_can_publish_selected_records() -> None:
    result = convert_chinese_gushiwen(
        [_raw_record("poem-1")],
        limit=1,
        publish=True,
    )

    assert result.dataset.defaults.publish is True
    assert result.manifest["selection"]["publish"] is True


def test_conversion_excludes_non_poetry_and_duplicates() -> None:
    records = [
        _raw_record("poem-1"),
        _raw_record("poem-duplicate"),
        _raw_record(
            "prose-1",
            title="岳阳楼记",
            writer="范仲淹",
            types=["古文观止", "初中文言文"],
        ),
    ]

    result = convert_chinese_gushiwen(records, limit=1)

    assert [record.external_id for record in result.dataset.records] == ["poem-1"]
    assert result.manifest["selection"]["excluded_non_poetry"] == 1
    assert result.manifest["selection"]["duplicate_candidates"] == 1
    assert result.manifest["selection"]["eligible_unique_records"] == 1


def test_conversion_accepts_high_confidence_untagged_poem() -> None:
    result = convert_chinese_gushiwen(
        [
            _raw_record(
                "poem-1",
                title="山中",
                content="荆溪白石出，天寒红叶稀。\n山路元无雨，空翠湿人衣。",
                types=["秋天", "纪行", "写景"],
            )
        ],
        limit=1,
    )

    assert [record.external_id for record in result.dataset.records] == ["poem-1"]
    assert result.manifest["selection"]["excluded_non_poetry"] == 0


def test_conversion_rejects_indented_prose_without_explicit_marker() -> None:
    result = convert_chinese_gushiwen(
        [
            _raw_record("poem-1"),
            _raw_record(
                "prose-1",
                title="诫子书",
                writer="诸葛亮",
                content="　　夫君子之行，静以修身，俭以养德。",
                types=["修身", "立志"],
            ),
        ],
        limit=1,
    )

    assert [record.external_id for record in result.dataset.records] == ["poem-1"]
    assert result.manifest["selection"]["excluded_non_poetry"] == 1
    assert result.manifest["selection"]["excluded_explicit_prose"] == 0
    assert result.manifest["selection"]["excluded_low_confidence"] == 1


def test_multi_file_conversion_tracks_each_source_shard(tmp_path: Any) -> None:
    first = tmp_path / "guwen0-1000.json"
    second = tmp_path / "guwen1001-2000.json"
    first.write_text(
        json.dumps(_raw_record("poem-1"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            _raw_record(
                "poem-2",
                title="山居秋暝",
                writer="王维",
                content="空山新雨后，天气晚来秋。\n明月松间照，清泉石上流。",
                types=["唐诗三百首", "山水"],
            ),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = convert_chinese_gushiwen_files([first, second], limit=2)

    assert len(result.dataset.records) == 2
    assert [item["file_name"] for item in result.manifest["source"]["files"]] == [
        "guwen/guwen0-1000.json",
        "guwen/guwen1001-2000.json",
    ]
    assert result.dataset.records[0].raw_payload is not None
    assert result.dataset.records[1].raw_payload is not None
    assert (
        result.dataset.records[1].raw_payload["source_file"]
        == "guwen/guwen1001-2000.json"
    )
    assert result.dataset.records[1].source_url is not None
    assert result.dataset.records[1].source_url.endswith(
        "/guwen/guwen1001-2000.json",
    )


def test_stratified_selection_covers_dynasties_and_length_buckets() -> None:
    records: list[dict[str, Any]] = []
    for dynasty, writer, marker in (
        ("唐代", "李白", "唐诗三百首"),
        ("宋代", "苏轼", "宋词三百首"),
        ("先秦", "佚名", "诗经"),
    ):
        for bucket, content in (
            ("short", "短句。" * 5),
            ("medium", "中篇。" * 40),
            ("long", "长篇。" * 200),
        ):
            records.append(
                _raw_record(
                    f"{dynasty}-{bucket}",
                    title=f"{dynasty}{bucket}",
                    dynasty=dynasty,
                    writer=writer,
                    content=content,
                    types=[marker, bucket],
                )
            )

    result = convert_chinese_gushiwen(records, limit=6)

    assert len(result.dataset.records) == 6
    assert result.manifest["selection"]["dynasty_quotas"] == {
        "先秦": 2,
        "唐": 2,
        "宋": 2,
    }
    assert result.manifest["distributions"]["dynasties"] == {
        "先秦": 2,
        "唐": 2,
        "宋": 2,
    }
    assert result.manifest["distributions"]["content_lengths"] == {
        "short": 3,
        "medium": 3,
        "long": 0,
    }


def test_conversion_rejects_insufficient_candidates() -> None:
    with pytest.raises(ValueError, match="fewer than limit"):
        convert_chinese_gushiwen([_raw_record("poem-1")], limit=2)
