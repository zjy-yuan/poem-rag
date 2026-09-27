from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import sha256_text
from app.models.author import Author
from app.models.dynasty import Dynasty
from app.models.poem import Poem, PoemStatus
from app.models.source import PoemSource
from app.models.version import PoemVersion
from app.schemas.domain_label_sampling import (
    DomainLabelSamplingManifest,
    DomainLabelSamplingManualLabels,
    DomainLabelSamplingRecord,
    DomainLabelSamplingStratum,
)

MANIFEST_VERSION = "domain-label-gold-v2-sampling"
MANIFEST_NAME = "领域标签独立盲标采样清单"
MANIFEST_DESCRIPTION = (
    "从真实已发布作品中按固定种子和分层策略抽取候选，供人工独立标注。"
    "清单不包含 AI 预测、公开数据集标签或已审核标签。"
)
MANIFEST_SELECTION_STRATEGY = (
    "排除首批同源金标准；按标题或行数识别长文本与词曲，其余按唐、宋、其他"
    "朝代分层；每层按 seed 与 external_id 的 SHA-256 排序，单个作者最多 2 首，"
    "名额不足时在同一分层内按确定性顺序补足。"
)

STRATUM_ORDER = (
    DomainLabelSamplingStratum.TANG_POEM,
    DomainLabelSamplingStratum.SONG_WORK,
    DomainLabelSamplingStratum.OTHER_DYNASTY,
    DomainLabelSamplingStratum.LONG_TEXT_OR_CI,
)
LONG_TEXT_LINE_THRESHOLD = 10
MAX_POEMS_PER_AUTHOR = 2


@dataclass(frozen=True, slots=True)
class _Candidate:
    external_id: str
    poem_id: int
    version_id: int
    title: str
    author: str
    dynasty: str
    source_name: str | None
    source_url: str | None
    content_hash: str
    content: str
    stratum: DomainLabelSamplingStratum


class DomainLabelSamplingPreparer:
    """Build a deterministic, label-blind sampling manifest from the live corpus."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepare(
        self,
        *,
        source_key: str,
        count: int,
        seed: str,
        excluded_external_ids: set[str],
    ) -> DomainLabelSamplingManifest:
        if count < len(STRATUM_ORDER):
            raise ValueError(f"count 至少为 {len(STRATUM_ORDER)}")

        candidates = await self._load_candidates(
            source_key=source_key,
            excluded_external_ids=excluded_external_ids,
        )
        selected = self._select(candidates, count=count, seed=seed)
        records = [self._to_record(candidate) for candidate in selected]
        return DomainLabelSamplingManifest(
            version=MANIFEST_VERSION,
            name=MANIFEST_NAME,
            description=MANIFEST_DESCRIPTION,
            selection_strategy=MANIFEST_SELECTION_STRATEGY,
            source_key=source_key,
            random_seed=seed,
            requested_count=count,
            sampled_count=len(records),
            generated_at=datetime.now(UTC),
            excluded_external_ids=sorted(excluded_external_ids),
            records=records,
        )

    async def _load_candidates(
        self,
        *,
        source_key: str,
        excluded_external_ids: set[str],
    ) -> list[_Candidate]:
        rows = await self.session.execute(
            select(
                Poem,
                PoemVersion,
                PoemSource,
                Author.name,
                Dynasty.name,
            )
            .join(
                PoemVersion,
                and_(
                    PoemVersion.poem_id == Poem.id,
                    PoemVersion.version_no == Poem.version_no,
                ),
            )
            .join(PoemSource, PoemSource.poem_id == Poem.id)
            .outerjoin(Author, Author.id == Poem.author_id)
            .outerjoin(Dynasty, Dynasty.id == Poem.dynasty_id)
            .where(
                Poem.status == PoemStatus.PUBLISHED.value,
                Poem.deleted_at.is_(None),
                PoemSource.source_key == source_key,
                PoemSource.external_id.is_not(None),
            )
            .order_by(PoemSource.external_id, Poem.id)
        )

        candidates: list[_Candidate] = []
        for poem, version, source, author_name, dynasty_name in rows.all():
            external_id = source.external_id
            if external_id is None or external_id in excluded_external_ids:
                continue
            content = version.snapshot.get("content") if version.snapshot else None
            if not isinstance(content, str) or not content:
                raise ValueError(
                    "当前版本缺少正文，无法建立盲标清单: "
                    f"external_id={external_id}"
                )
            actual_content_hash = sha256_text(content)
            if source.content_hash != actual_content_hash:
                raise ValueError(
                    "来源内容哈希与当前版本正文不一致，必须先复核版本: "
                    f"external_id={external_id}"
                )
            line_count = len(content.splitlines())
            dynasty = dynasty_name or source.raw_dynasty_name or "未知"
            title = poem.title
            candidates.append(
                _Candidate(
                    external_id=external_id,
                    poem_id=poem.id,
                    version_id=version.id,
                    title=title,
                    author=author_name or source.raw_author_name or "未知",
                    dynasty=dynasty,
                    source_name=source.source_name,
                    source_url=source.source_url,
                    content_hash=source.content_hash,
                    content=content,
                    stratum=_classify_stratum(
                        dynasty=dynasty,
                        title=title,
                        line_count=line_count,
                    ),
                )
            )
        return candidates

    @staticmethod
    def _select(
        candidates: list[_Candidate],
        *,
        count: int,
        seed: str,
    ) -> list[_Candidate]:
        quotas = _allocate_quotas(count)
        by_stratum = {stratum: [] for stratum in STRATUM_ORDER}
        for candidate in candidates:
            by_stratum[candidate.stratum].append(candidate)
        for stratum in STRATUM_ORDER:
            by_stratum[stratum].sort(
                key=lambda item: (
                    _selection_digest(seed, item.external_id),
                    item.external_id,
                )
            )

        selected: list[_Candidate] = []
        selected_ids: set[str] = set()
        author_counts: dict[str, int] = {}
        for stratum in STRATUM_ORDER:
            quota = quotas[stratum]
            chosen = _select_from_stratum(
                by_stratum[stratum],
                quota=quota,
                author_counts=author_counts,
            )
            if len(chosen) < quota:
                raise ValueError(
                    "分层候选不足，无法完成确定性采样: "
                    f"stratum={stratum.value} required={quota} available={len(chosen)}"
                )
            for candidate in chosen:
                selected.append(candidate)
                selected_ids.add(candidate.external_id)
                author_counts[candidate.author] = author_counts.get(candidate.author, 0) + 1

        if len(selected) != count or len(selected_ids) != count:
            raise ValueError(
                "采样数量不一致: "
                f"requested={count} selected={len(selected)} unique={len(selected_ids)}"
            )
        return selected

    @staticmethod
    def _to_record(candidate: _Candidate) -> DomainLabelSamplingRecord:
        lines = candidate.content.splitlines()
        return DomainLabelSamplingRecord(
            external_id=candidate.external_id,
            poem_id=candidate.poem_id,
            version_id=candidate.version_id,
            title=candidate.title,
            author=candidate.author,
            dynasty=candidate.dynasty,
            source_name=candidate.source_name,
            source_url=candidate.source_url,
            content_hash=candidate.content_hash,
            line_count=len(lines),
            line_numbered_content="\n".join(
                f"{line_number} | {line}"
                for line_number, line in enumerate(lines, start=1)
            ),
            stratum=candidate.stratum,
            manual_labels=DomainLabelSamplingManualLabels(),
        )


def _classify_stratum(
    *,
    dynasty: str,
    title: str,
    line_count: int,
) -> DomainLabelSamplingStratum:
    if line_count >= LONG_TEXT_LINE_THRESHOLD or "·" in title:
        return DomainLabelSamplingStratum.LONG_TEXT_OR_CI
    if dynasty == "唐":
        return DomainLabelSamplingStratum.TANG_POEM
    if dynasty == "宋":
        return DomainLabelSamplingStratum.SONG_WORK
    return DomainLabelSamplingStratum.OTHER_DYNASTY


def _allocate_quotas(count: int) -> dict[DomainLabelSamplingStratum, int]:
    base, remainder = divmod(count, len(STRATUM_ORDER))
    return {
        stratum: base + (1 if index < remainder else 0)
        for index, stratum in enumerate(STRATUM_ORDER)
    }


def _select_from_stratum(
    candidates: list[_Candidate],
    *,
    quota: int,
    author_counts: dict[str, int],
) -> list[_Candidate]:
    chosen: list[_Candidate] = []
    local_author_counts: dict[str, int] = {}
    for candidate in candidates:
        effective_count = max(
            author_counts.get(candidate.author, 0),
            local_author_counts.get(candidate.author, 0),
        )
        if effective_count >= MAX_POEMS_PER_AUTHOR:
            continue
        chosen.append(candidate)
        local_author_counts[candidate.author] = effective_count + 1
        if len(chosen) == quota:
            return chosen

    chosen_ids = {item.external_id for item in chosen}
    for candidate in candidates:
        if candidate.external_id in chosen_ids:
            continue
        chosen.append(candidate)
        if len(chosen) == quota:
            break
    return chosen


def _selection_digest(seed: str, external_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{external_id}".encode()).hexdigest()
