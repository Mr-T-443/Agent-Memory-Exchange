"""Tests for record status lifecycle and decision supersession."""

from __future__ import annotations

from amx.memory.bundle import build_bundle
from amx.memory.ingest import ingest_record
from amx.memory.retrieval import search_memory
from amx.schema import RecordType
from amx.state.decision_log import record_decision
from amx.state.project_state import update_project_state
from amx.store import Store


def _bundle_titles(bundle, kind):
    return [s.title for s in bundle.slices if s.kind == kind]


def test_new_record_is_active_by_default(store):
    rec = ingest_record(store, "p1", RecordType.TASK, "wire rollback", "next step")
    assert rec.status is None
    fetched = store.get_record(rec.id)
    assert fetched.status is None and fetched.superseded_by_id is None


def test_closed_task_excluded_from_bundle_but_searchable(store, cfg):
    update_project_state(store, "p1", {"current_goal": "ship"})
    active = ingest_record(store, "p1", RecordType.TASK, "open task", "do this")
    closed = ingest_record(store, "p1", RecordType.TASK, "done task", "already shipped")
    store.set_record_status(closed.id, "done")

    bundle = build_bundle(store, "p1", cfg)
    titles = _bundle_titles(bundle, "record")
    assert "open task" in titles
    assert "done task" not in titles

    # Findable by search.
    found = {m.title for m in search_memory(store, "p1", "task", 10, cfg).matches}
    assert "done task" in found
    # Excluded from active-only search.
    active_found = {
        m.title for m in search_memory(store, "p1", "task", 10, cfg, active_only=True).matches
    }
    assert "done task" not in active_found
    assert "open task" in active_found


def test_status_transitions_and_reopen(store):
    rec = ingest_record(store, "p1", RecordType.BUG, "crash", "null token")
    store.set_record_status(rec.id, "resolved")
    assert store.get_record(rec.id).status == "resolved"
    store.set_record_status(rec.id, "open")
    assert store.get_record(rec.id).status == "open"
    # Verify open status is active.
    assert store.recent_records("p1", 10, active_only=True)[0].id == rec.id


def test_superseded_decision_excluded_from_bundle(store, cfg):
    update_project_state(store, "p1", {"current_goal": "ship"})
    old = record_decision(store, "p1", "Use JSON files", "simple")
    record_decision(store, "p1", "Use SQLite", "concurrent + queryable", supersedes_id=old.id)

    bundle = build_bundle(store, "p1", cfg)
    decisions = _bundle_titles(bundle, "decision")
    assert "Use SQLite" in decisions
    assert "Use JSON files" not in decisions

    # Verify superseded decision is preserved in full log.
    all_titles = {d.title for d in store.list_decisions("p1", 10)}
    assert "Use JSON files" in all_titles
    active = {d.title for d in store.list_decisions("p1", 10, exclude_superseded=True)}
    assert "Use JSON files" not in active


def test_supersede_marks_record(store):
    old = ingest_record(store, "p1", RecordType.RESEARCH, "BM25 enough", "v1 note")
    new = ingest_record(store, "p1", RecordType.RESEARCH, "BM25 enough", "v2 refined note")
    store.set_record_status(old.id, "superseded", superseded_by_id=new.id)
    fetched = store.get_record(old.id)
    assert fetched.status == "superseded"
    assert fetched.superseded_by_id == new.id


def test_lifecycle_columns_persist_across_reopen(store, cfg):
    rec = ingest_record(store, "p1", RecordType.TASK, "persist me", "body")
    store.set_record_status(rec.id, "dropped")
    store.close()
    reopened = Store(cfg.db_path)
    assert reopened.get_record(rec.id).status == "dropped"
    reopened.close()
