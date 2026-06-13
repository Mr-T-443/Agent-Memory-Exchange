"""Tests for basic memory record ingestion and search availability."""

from __future__ import annotations

from amx.memory.ingest import ingest_record
from amx.schema import RecordType


def test_ingest_stores_record(store):
    record = ingest_record(
        store, "p1", RecordType.TASK, "Implement ranked search", "Use FTS5 BM25.",
        entities=["retrieval.py"],
    )
    assert record.id is not None
    assert record.token_estimate > 0

    recent = store.recent_records("p1", limit=5)
    assert len(recent) == 1
    assert recent[0].title == "Implement ranked search"
    assert recent[0].entities == ["retrieval.py"]


def test_ingested_record_is_searchable(store):
    ingest_record(store, "p1", RecordType.DECISION, "Use SQLite", "Local-first metadata store.")
    hits = store.search_records("p1", "sqlite", limit=5)
    assert len(hits) == 1
    assert hits[0][0].title == "Use SQLite"


def test_search_is_project_scoped(store):
    ingest_record(store, "p1", RecordType.TASK, "Router firmware", "OTA update flow.")
    assert store.search_records("p2", "router", limit=5) == []
