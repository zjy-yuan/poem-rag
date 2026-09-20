from __future__ import annotations

import pytest
from app.core.text import normalize_content, sha256_text
from app.models.chunk import ChunkGranularity
from app.services.chunking import AnnotationChunkInput, chunk_poem


def test_short_poem_keeps_full_poem_and_line_chunks() -> None:
    content = "床前明月光，疑是地上霜。\n举头望明月，低头思故乡。"

    chunks = chunk_poem(content)
    poem_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.POEM]
    line_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.LINE]

    assert len(poem_chunks) == 1
    assert poem_chunks[0].chunk_index == 0
    assert poem_chunks[0].text == content
    assert poem_chunks[0].line_start == 1
    assert poem_chunks[0].line_end == 2
    assert [chunk.text for chunk in line_chunks] == content.splitlines()
    assert [chunk.chunk_index for chunk in line_chunks] == [0, 1]
    assert [(chunk.line_start, chunk.line_end) for chunk in line_chunks] == [(1, 1), (2, 2)]
    assert poem_chunks[0].normalized_text == normalize_content(content)
    assert poem_chunks[0].content_hash == sha256_text(content)


def test_long_poem_uses_paragraph_and_length_boundaries() -> None:
    content = (
        "第一段第一行。第一段第二行。\n"
        "第一段第三行。\n"
        "\n"
        "第二段第一行。第二段第二行。"
    )

    chunks = chunk_poem(content, max_poem_chars=30)
    poem_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.POEM]

    assert [chunk.text for chunk in poem_chunks] == [
        "第一段第一行。第一段第二行。\n第一段第三行。",
        "第二段第一行。第二段第二行。",
    ]
    assert [(chunk.line_start, chunk.line_end) for chunk in poem_chunks] == [(1, 2), (4, 4)]
    assert [chunk.chunk_index for chunk in poem_chunks] == [0, 1]


def test_long_line_splits_at_strong_punctuation_before_hard_limit() -> None:
    content = "甲甲甲甲甲。乙乙乙乙乙。丙丙丙丙丙。"

    chunks = chunk_poem(content, max_line_chars=8)
    line_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.LINE]

    assert [chunk.text for chunk in line_chunks] == [
        "甲甲甲甲甲。",
        "乙乙乙乙乙。",
        "丙丙丙丙丙。",
    ]
    assert all(chunk.line_start == 1 and chunk.line_end == 1 for chunk in line_chunks)


def test_annotation_chunks_keep_annotation_id_and_line_range() -> None:
    content = "床前明月光，疑是地上霜。"
    annotation = AnnotationChunkInput(
        annotation_id=42,
        content="第一段赏析。\n\n第二段赏析。",
        line_start=1,
        line_end=1,
    )

    chunks = chunk_poem(content, annotations=[annotation], max_note_chars=20)
    note_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.NOTE]

    assert [chunk.text for chunk in note_chunks] == ["第一段赏析。", "第二段赏析。"]
    assert [chunk.chunk_index for chunk in note_chunks] == [0, 1]
    assert all(chunk.annotation_id == 42 for chunk in note_chunks)
    assert all(chunk.line_start == 1 and chunk.line_end == 1 for chunk in note_chunks)


def test_multiple_annotations_share_contiguous_note_indexes() -> None:
    annotations = [
        AnnotationChunkInput(annotation_id=1, content="First note."),
        AnnotationChunkInput(annotation_id=2, content="Second note."),
    ]

    chunks = chunk_poem("Poem line.", annotations=annotations)
    note_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.NOTE]

    assert [(chunk.annotation_id, chunk.chunk_index) for chunk in note_chunks] == [
        (1, 0),
        (2, 1),
    ]


def test_crlf_and_blank_lines_do_not_shift_source_line_numbers() -> None:
    content = "第一行。\r\n\r\n第三行。"

    chunks = chunk_poem(content)
    poem_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.POEM]
    line_chunks = [chunk for chunk in chunks if chunk.granularity == ChunkGranularity.LINE]

    assert len(poem_chunks) == 1
    assert poem_chunks[0].line_start == 1
    assert poem_chunks[0].line_end == 3
    assert [(chunk.line_start, chunk.line_end) for chunk in line_chunks] == [(1, 1), (3, 3)]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "at least one non-empty line"),
        ("   \n\t", "at least one non-empty line"),
    ],
)
def test_empty_content_is_rejected(content: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        chunk_poem(content)


def test_invalid_annotation_range_is_rejected() -> None:
    annotation = AnnotationChunkInput(
        annotation_id=1,
        content="赏析",
        line_start=2,
        line_end=1,
    )

    with pytest.raises(ValueError, match="line_start"):
        chunk_poem("正文。", annotations=[annotation])
