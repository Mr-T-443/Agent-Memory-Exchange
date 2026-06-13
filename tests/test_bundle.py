"""Tests for token-budgeted project context bundling."""

from __future__ import annotations

from amx.memory.bundle import build_bundle
from amx.memory.ingest import ingest_record
from amx.schema import RecordType
from amx.state.decision_log import record_decision
from amx.state.project_state import update_project_state


def test_cold_start_returns_graceful_payload(store, cfg):
    bundle = build_bundle(store, "fresh-project", cfg)
    assert bundle.cold_start is True
    assert bundle.used_tokens == 0
    assert bundle.slices[0].kind == "project_state"


def test_bundle_carries_capture_reminder(store, cfg):
    # Verify capture reminder exists on both cold and warm paths.
    from amx.adoption import CAPTURE_REMINDER

    assert build_bundle(store, "fresh-project", cfg).capture_reminder == CAPTURE_REMINDER
    update_project_state(store, "p1", {"current_goal": "Build AMX"})
    assert build_bundle(store, "p1", cfg).capture_reminder == CAPTURE_REMINDER


def test_bundle_order_state_summary_decisions(store, cfg):
    update_project_state(store, "p1", {"current_goal": "Build AMX"})
    record_decision(store, "p1", "Use SQLite", "Local-first storage.")
    bundle = build_bundle(store, "p1", cfg)
    kinds = [s.kind for s in bundle.slices]
    assert kinds[0] == "project_state"
    assert "summary" in kinds
    assert "decision" in kinds
    assert kinds.index("summary") < kinds.index("decision")


def test_bundle_respects_budget(store, cfg):
    update_project_state(store, "p1", {"current_goal": "Build AMX"})
    for i in range(20):
        ingest_record(store, "p1", RecordType.TASK, f"Task {i}", "word " * 200)
    bundle = build_bundle(store, "p1", cfg, budget_tokens=500)
    assert bundle.used_tokens <= 500
    assert len(bundle.slices) < 21


def test_bundle_with_query_uses_search(store, cfg):
    update_project_state(store, "p1", {"current_goal": "Build AMX"})
    ingest_record(store, "p1", RecordType.TASK, "Router firmware updater",
                  "OTA update flow complete; rollback pending.")
    bundle = build_bundle(store, "p1", cfg, query="router updater")
    match_slices = [s for s in bundle.slices if s.kind == "match"]
    assert match_slices
    assert match_slices[0].title == "Router firmware updater"
