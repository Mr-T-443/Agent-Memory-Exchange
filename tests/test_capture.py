"""Tests for record capture, deduplication, and database migrations."""

from __future__ import annotations

from amx.config import TYPE_WEIGHTS
from amx.memory.bundle import build_bundle
from amx.memory.ingest import ingest_record
from amx.schema import RecordType
from amx.state.decision_log import record_decision
from amx.store import Store
from amx.utils.text import content_hash, normalize


# Deduplication tests.

def test_exact_duplicate_is_deduped(store):
    first = ingest_record(store, "p1", RecordType.TASK, "Wire rollback", "next step")
    second = ingest_record(store, "p1", RecordType.TASK, "Wire rollback", "next step")
    assert first.id == second.id
    assert second.deduped is True
    assert first.deduped is False
    assert store.record_count("p1") == 1


def test_normalized_duplicate_is_deduped(store):
    ingest_record(store, "p1", RecordType.BUG, "OTA hangs", "pipe closes on Windows")
    dup = ingest_record(
        store, "p1", RecordType.BUG, "  OTA   HANGS ", "Pipe closes on Windows\n"
    )
    assert dup.deduped is True
    assert store.record_count("p1") == 1


def test_different_body_is_not_deduped(store):
    ingest_record(store, "p1", RecordType.TASK, "Wire rollback", "next step")
    other = ingest_record(store, "p1", RecordType.TASK, "Wire rollback", "after release")
    assert other.deduped is False
    assert store.record_count("p1") == 2


def test_dedup_is_per_project(store):
    a = ingest_record(store, "p1", RecordType.RESEARCH, "BM25 is enough", "skip embeddings")
    b = ingest_record(store, "p2", RecordType.RESEARCH, "BM25 is enough", "skip embeddings")
    assert b.deduped is False
    assert a.id != b.id
    assert store.record_count("p1") == 1 and store.record_count("p2") == 1


# Record type tests.

def test_new_types_ingest_and_search(store):
    ingest_record(store, "p1", RecordType.BUG, "Login crash", "null token on refresh")
    ingest_record(store, "p1", RecordType.ARCHITECTURE, "Hexagonal layout", "ports and adapters")
    ingest_record(store, "p1", RecordType.RESEARCH, "FTS5 BM25 notes", "ranking is sufficient")
    hits = store.search_records("p1", "bm25 ranking", limit=5)
    assert any(h[0].type == RecordType.RESEARCH for h in hits)


def test_every_record_type_has_a_ranking_weight():
    for rt in RecordType:
        assert rt in TYPE_WEIGHTS, f"missing TYPE_WEIGHTS entry for {rt}"


# Bundle uniqueness tests.

def test_decision_appears_once_in_bundle_no_query(store, cfg):
    record_decision(store, "p1", "Use SQLite", "Local-first storage.")
    bundle = build_bundle(store, "p1", cfg)
    titled = [s for s in bundle.slices if s.title == "Use SQLite"]
    assert len(titled) == 1
    assert titled[0].kind == "decision"
    assert all(s.kind != "record" for s in bundle.slices)


def test_decision_appears_once_in_bundle_with_query(store, cfg):
    record_decision(store, "p1", "Use SQLite", "Local-first storage.")
    bundle = build_bundle(store, "p1", cfg, query="sqlite")
    match_titles = [s.title for s in bundle.slices if s.kind == "match"]
    assert "Use SQLite" not in match_titles
    assert any(s.kind == "decision" and s.title == "Use SQLite" for s in bundle.slices)


# Content hash backfill tests.

def test_backfill_populates_null_content_hash(store, cfg):
    ingest_record(store, "p1", RecordType.TASK, "t", "b")
    # Simulate a pre-v2 row written without a content hash.
    store._conn.execute("UPDATE records SET content_hash = NULL")
    store._conn.commit()
    store.close()

    # Backfill runs on initialization.
    reopened = Store(cfg.db_path)
    found = reopened.find_record_by_hash("p1", content_hash("task", "t", "b"))
    assert found is not None
    # Verify deduplication against backfilled row.
    again = ingest_record(reopened, "p1", RecordType.TASK, "t", "b")
    assert again.deduped is True
    reopened.close()


def test_normalize_helper():
    assert normalize("  Hello   World \n") == "hello world"
