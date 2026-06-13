"""Tests for project state updates and key-value merges."""

from __future__ import annotations

from amx.state.project_state import get_project_state, update_project_state


def test_state_starts_empty(store):
    assert get_project_state(store, "p1") == {}


def test_patch_merges_shallowly(store):
    update_project_state(store, "p1", {"current_goal": "Build MCP server"})
    state = update_project_state(store, "p1", {"active_task": "Ranked search"})
    assert state == {"current_goal": "Build MCP server", "active_task": "Ranked search"}


def test_null_removes_key(store):
    update_project_state(store, "p1", {"a": 1, "b": 2})
    state = update_project_state(store, "p1", {"a": None})
    assert state == {"b": 2}


def test_state_updates_are_logged(store):
    update_project_state(store, "p1", {"current_goal": "x"})
    recent = store.recent_records("p1", limit=5)
    assert any(r.title == "state_update" for r in recent)
