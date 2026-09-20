from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.core.text import normalize_content, sha256_text
from app.models.chunk import ChunkGranularity

CHUNK_STRATEGY = "structural-v1"
DEFAULT_MAX_POEM_CHARS = 480
DEFAULT_MAX_LINE_CHARS = 200
DEFAULT_MAX_NOTE_CHARS = 800

_STRONG_BOUNDARIES = "。！？!?；;"
_WEAK_BOUNDARIES = "，,、"
_BLANK_LINE = re.compile(r"\n\s*\n")


@dataclass(frozen=True, slots=True)
class AnnotationChunkInput:
    """An annotation that should be included in a version's chunk set."""

    annotation_id: int
    content: str
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A database-independent chunk ready to be persisted."""

    granularity: ChunkGranularity
    chunk_index: int
    text: str
    normalized_text: str
    content_hash: str
    line_start: int | None
    line_end: int | None
    annotation_id: int | None = None


@dataclass(frozen=True, slots=True)
class _ContentLine:
    number: int
    text: str


def chunk_poem(
    content: str,
    *,
    annotations: Iterable[AnnotationChunkInput] = (),
    max_poem_chars: int = DEFAULT_MAX_POEM_CHARS,
    max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
    max_note_chars: int = DEFAULT_MAX_NOTE_CHARS,
) -> list[ChunkDraft]:
    """Build deterministic structural chunks for one immutable poem version.

    The v1 strategy deliberately uses only observable structure: non-empty
    lines, blank-line paragraphs and length boundaries. Rhyme-aware splitting
    belongs to a later strategy once a reliable rhyme source is available.
    """

    _validate_limit("max_poem_chars", max_poem_chars)
    _validate_limit("max_line_chars", max_line_chars)
    _validate_limit("max_note_chars", max_note_chars)

    lines = _parse_lines(content)
    if not lines:
        raise ValueError("content must contain at least one non-empty line")

    drafts = _build_poem_chunks(lines, max_poem_chars)
    drafts.extend(_build_line_chunks(lines, max_line_chars))
    drafts.extend(_build_note_chunks(annotations, max_note_chars))
    return drafts


def _build_poem_chunks(lines: Sequence[_ContentLine], max_chars: int) -> list[ChunkDraft]:
    full_text = _join_lines(lines)
    if len(full_text) <= max_chars:
        return [
            _make_draft(
                granularity=ChunkGranularity.POEM,
                chunk_index=0,
                text=full_text,
                line_start=lines[0].number,
                line_end=lines[-1].number,
            )
        ]

    drafts: list[ChunkDraft] = []
    for paragraph in _paragraphs(lines):
        paragraph_text = _join_lines(paragraph)
        if len(paragraph_text) <= max_chars:
            drafts.append(
                _make_draft(
                    granularity=ChunkGranularity.POEM,
                    chunk_index=len(drafts),
                    text=paragraph_text,
                    line_start=paragraph[0].number,
                    line_end=paragraph[-1].number,
                )
            )
            continue

        current: list[_ContentLine] = []
        for line in paragraph:
            if len(line.text) > max_chars:
                _flush_poem_group(drafts, current)
                current = []
                for piece in _split_text(line.text, max_chars):
                    drafts.append(
                        _make_draft(
                            granularity=ChunkGranularity.POEM,
                            chunk_index=len(drafts),
                            text=piece,
                            line_start=line.number,
                            line_end=line.number,
                        )
                    )
                continue

            candidate = [*current, line]
            if current and len(_join_lines(candidate)) > max_chars:
                _flush_poem_group(drafts, current)
                current = [line]
            else:
                current = candidate
        _flush_poem_group(drafts, current)

    return drafts


def _build_line_chunks(lines: Sequence[_ContentLine], max_chars: int) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for line in lines:
        for piece in _split_text(line.text, max_chars):
            drafts.append(
                _make_draft(
                    granularity=ChunkGranularity.LINE,
                    chunk_index=len(drafts),
                    text=piece,
                    line_start=line.number,
                    line_end=line.number,
                )
            )
    return drafts


def _build_note_chunks(
    annotations: Iterable[AnnotationChunkInput],
    max_chars: int,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for annotation in annotations:
        _validate_annotation_range(annotation)
        for piece in _split_text(annotation.content, max_chars):
            drafts.append(
                _make_draft(
                    granularity=ChunkGranularity.NOTE,
                    chunk_index=len(drafts),
                    text=piece,
                    line_start=annotation.line_start,
                    line_end=annotation.line_end,
                    annotation_id=annotation.annotation_id,
                )
            )
    return drafts


def _flush_poem_group(drafts: list[ChunkDraft], group: Sequence[_ContentLine]) -> None:
    if not group:
        return
    drafts.append(
        _make_draft(
            granularity=ChunkGranularity.POEM,
            chunk_index=len(drafts),
            text=_join_lines(group),
            line_start=group[0].number,
            line_end=group[-1].number,
        )
    )


def _make_draft(
    *,
    granularity: ChunkGranularity,
    chunk_index: int,
    text: str,
    line_start: int | None,
    line_end: int | None,
    annotation_id: int | None = None,
) -> ChunkDraft:
    return ChunkDraft(
        granularity=granularity,
        chunk_index=chunk_index,
        text=text,
        normalized_text=normalize_content(text),
        content_hash=sha256_text(text),
        line_start=line_start,
        line_end=line_end,
        annotation_id=annotation_id,
    )


def _parse_lines(content: str) -> list[_ContentLine]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return [
        _ContentLine(number=index, text=line.strip())
        for index, line in enumerate(normalized.split("\n"), start=1)
        if line.strip()
    ]


def _paragraphs(lines: Sequence[_ContentLine]) -> list[list[_ContentLine]]:
    paragraphs: list[list[_ContentLine]] = []
    current: list[_ContentLine] = []
    previous_number: int | None = None
    for line in lines:
        if previous_number is not None and line.number > previous_number + 1:
            if current:
                paragraphs.append(current)
                current = []
        current.append(line)
        previous_number = line.number
    if current:
        paragraphs.append(current)
    return paragraphs


def _join_lines(lines: Sequence[_ContentLine]) -> str:
    return "\n".join(line.text for line in lines)


def _split_text(text: str, max_chars: int) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    paragraphs = [paragraph.strip() for paragraph in _BLANK_LINE.split(text)]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph]
    if len(paragraphs) > 1:
        return [
            piece
            for paragraph in paragraphs
            for piece in _split_text(paragraph, max_chars)
        ]

    if len(text) <= max_chars:
        return [text]

    minimum_cut = max(1, max_chars // 2)
    cut = _find_boundary(text, max_chars, minimum_cut, _STRONG_BOUNDARIES)
    if cut is None:
        cut = _find_boundary(text, max_chars, minimum_cut, _WEAK_BOUNDARIES)
    if cut is None:
        cut = max_chars

    head = text[:cut].strip()
    tail = text[cut:].strip()
    return [head, *_split_text(tail, max_chars)] if tail else [head]


def _find_boundary(
    text: str,
    max_chars: int,
    minimum_cut: int,
    boundaries: str,
) -> int | None:
    for index in range(min(max_chars, len(text)) - 1, minimum_cut - 1, -1):
        if text[index] in boundaries:
            return index + 1
    return None


def _validate_limit(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")


def _validate_annotation_range(annotation: AnnotationChunkInput) -> None:
    if (annotation.line_start is None) != (annotation.line_end is None):
        raise ValueError("annotation line_start and line_end must both be set or both be empty")
    if (
        annotation.line_start is not None
        and annotation.line_end is not None
        and annotation.line_start > annotation.line_end
    ):
        raise ValueError("annotation line_start must not be greater than line_end")
