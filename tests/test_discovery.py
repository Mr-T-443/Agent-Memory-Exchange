"""Tests for cross-project discovery and ranking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from amx.memory.discovery import discover_projects, recent_projects
from amx.schema import MemoryRecord, RecordType, Summary


def _ts(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def _seed(store, pid, *, name=None, root_path=None, records=(), state=None, summary=None):
    store.ensure_project(pid, name=name, root_path=root_path)
    for title, body, ts in records:
        store.insert_record(
            MemoryRecord(
                project_id=pid, type=RecordType.THREAD, title=title, body=body, created_at=ts
            )
        )
    if state is not None:
        store.set_state(pid, state)
    if summary is not None:
        store.insert_summary(Summary(project_id=pid, body=summary))


def test_empty_store_returns_no_projects(store, cfg):
    result = discover_projects(store, "anything", cfg)
    assert result.candidates == []
    assert result.note == "No projects found."


def test_no_description_lists_recent_by_activity(store, cfg):
    _seed(store, "p-old", name="old", records=[("a", "x", _ts(30))])
    _seed(store, "p-mid", name="mid", records=[("b", "y", _ts(10))])
    _seed(store, "p-new", name="new", records=[("c", "z", _ts(1))])

    result = discover_projects(store, None, cfg)
    order = [c.project_id for c in result.candidates]
    assert order == ["p-new", "p-mid", "p-old"]
    assert result.query is None


def test_fuzzy_description_ranks_correct_project_top3(store, cfg):
    _seed(
        store,
        "p-yt",
        name="grabber",
        records=[("YouTube downloader", "fetch and save youtube videos as mp4", _ts(2))],
    )
    for i in range(10):
        _seed(store, f"p-{i}", name=f"proj{i}", records=[(f"topic {i}", f"unrelated body {i}", _ts(5))])

    result = discover_projects(store, "the youtube downloader thing", cfg)
    top3 = [c.project_id for c in result.candidates[:3]]
    assert "p-yt" in top3
    assert result.candidates[0].project_id == "p-yt"


def test_discovery_is_deterministic(store, cfg):
    for i in range(5):
        _seed(store, f"p-{i}", name=f"p{i}", records=[("router firmware", f"update flow {i}", _ts(i + 1))])
    a = discover_projects(store, "router firmware update", cfg)
    b = discover_projects(store, "router firmware update", cfg)
    assert [(c.project_id, c.score) for c in a.candidates] == [
        (c.project_id, c.score) for c in b.candidates
    ]


def test_similar_names_both_surface(store, cfg):
    _seed(store, "p-ext-a", name="amx-extension", records=[("browser extension", "popup ui", _ts(1))])
    _seed(store, "p-ext-b", name="amx-ext", records=[("browser extension", "content script", _ts(1))])

    result = discover_projects(store, "browser extension", cfg)
    ids = {c.project_id for c in result.candidates}
    assert {"p-ext-a", "p-ext-b"} <= ids


def test_null_named_project_falls_back_to_basename_then_id(store, cfg):
    _seed(
        store,
        "path-abc",
        root_path="/home/u/projects/my-tool",
        records=[("widget design", "build the widget", _ts(1))],
    )
    _seed(store, "path-bare", records=[("widget design", "another widget", _ts(1))])

    result = discover_projects(store, "widget", cfg)
    by_id = {c.project_id: c for c in result.candidates}
    assert by_id["path-abc"].name == "my-tool"
    assert by_id["path-bare"].name == "path-bare"


def test_last_activity_is_derived(store, cfg):
    ts = _ts(3)
    _seed(store, "p-1", name="one", records=[("note", "body", ts)])
    result = discover_projects(store, "note", cfg)
    assert result.candidates[0].last_activity is not None
    # Activity reflects record timestamp.
    assert result.candidates[0].last_activity.startswith(ts.isoformat()[:10])


def test_score_floor_filters_weak_matches(store, cfg):
    _seed(store, "p-1", name="one", records=[("router", "firmware", _ts(1))])
    # Ensure no matches if floor is set too high.
    cfg.discovery_score_floor = 2.0
    result = discover_projects(store, "router", cfg)
    assert result.candidates == []
    assert result.note == "No confident match."


def test_summary_hierarchy(store, cfg):
    _seed(store, "p-sum", name="s", records=[("t", "b", _ts(1))], summary="Session: shipped OTA flow.")
    _seed(store, "p-state", name="st", records=[("t", "b", _ts(1))], state={"current_goal": "Build updater"})
    _seed(store, "p-bare", name="ba", records=[("Rollback design", "details", _ts(1))])

    result = discover_projects(store, "t OR Rollback", cfg)
    by_id = {c.project_id: c for c in result.candidates}
    assert by_id["p-sum"].summary == "Session: shipped OTA flow."
    assert by_id["p-state"].summary == "Build updater"
    assert by_id["p-bare"].summary == "Rollback design"


def test_recent_projects_empty_store(store, cfg):
    result = recent_projects(store, cfg)
    assert result.candidates == []
    assert result.note == "No projects found."
