"""Tests for record ranking scoring and tie-breakers."""

from __future__ import annotations

from datetime import timedelta

from amx.memory.ranking import rank_records
from amx.schema import MemoryRecord, RecordType, utcnow


def _record(id, type, title, days_old=0.0, entities=None):
    return MemoryRecord(
        id=id,
        project_id="p1",
        type=type,
        title=title,
        body=title,
        entities=entities or [],
        created_at=utcnow() - timedelta(days=days_old),
    )


def test_recency_breaks_relevance_ties(cfg):
    hits = [
        (_record(1, RecordType.TASK, "old task", days_old=30), -1.0),
        (_record(2, RecordType.TASK, "new task", days_old=0), -1.0),
    ]
    ranked = rank_records(hits, "task", cfg)
    assert ranked[0].record.id == 2


def test_type_weight_prefers_decisions_over_raw_events(cfg):
    hits = [
        (_record(1, RecordType.RAW_EVENT, "sqlite log line"), -1.0),
        (_record(2, RecordType.DECISION, "sqlite chosen"), -1.0),
    ]
    ranked = rank_records(hits, "sqlite", cfg)
    assert ranked[0].record.type == RecordType.DECISION


def test_entity_overlap_boosts_score(cfg):
    hits = [
        (_record(1, RecordType.TASK, "fix bug"), -1.0),
        (_record(2, RecordType.TASK, "fix bug", entities=["retrieval"]), -1.0),
    ]
    ranked = rank_records(hits, "retrieval bug", cfg)
    assert ranked[0].record.id == 2


def test_empty_hits(cfg):
    assert rank_records([], "anything", cfg) == []
