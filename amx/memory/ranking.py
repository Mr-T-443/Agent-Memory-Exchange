"""Deterministic ranking and scoring for memory records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from amx.config import TYPE_WEIGHTS, AMXConfig
from amx.schema import MemoryRecord, utcnow

_TOKEN_RE = re.compile(r"\w+")


@dataclass
class RankedRecord:
    record: MemoryRecord
    score: float


# Normalize BM25 ranks to 0..1 range.
def _normalize_bm25(ranks: list[float]) -> list[float]:
    if not ranks:
        return []
    lo, hi = min(ranks), max(ranks)
    if hi == lo:
        return [1.0] * len(ranks)
    return [(hi - r) / (hi - lo) for r in ranks]


def _recency_score(created_at: datetime, now: datetime, half_life_days: float) -> float:
    age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def _entity_overlap(query_tokens: set[str], entities: list[str]) -> float:
    if not query_tokens or not entities:
        return 0.0
    entity_tokens = {t.lower() for e in entities for t in _TOKEN_RE.findall(e)}
    if not entity_tokens:
        return 0.0
    inter = query_tokens & entity_tokens
    union = query_tokens | entity_tokens
    return len(inter) / len(union)


def rank_records(
    hits: list[tuple[MemoryRecord, float]],
    query: str,
    cfg: AMXConfig,
    now: datetime | None = None,
) -> list[RankedRecord]:
    if not hits:
        return []

    now = now or utcnow()
    w = cfg.weights
    query_tokens = {t.lower() for t in _TOKEN_RE.findall(query)}
    bm25_norms = _normalize_bm25([rank for _, rank in hits])

    ranked = []
    for (record, _), bm25_norm in zip(hits, bm25_norms):
        score = (
            w.relevance * bm25_norm
            + w.recency * _recency_score(record.created_at, now, cfg.recency_half_life_days)
            + w.type_weight * TYPE_WEIGHTS.get(record.type, 0.5)
            + w.entity_overlap * _entity_overlap(query_tokens, record.entities)
        )
        ranked.append(RankedRecord(record=record, score=round(score, 6)))

    ranked.sort(key=lambda r: (-r.score, -(r.record.id or 0)))
    return ranked
