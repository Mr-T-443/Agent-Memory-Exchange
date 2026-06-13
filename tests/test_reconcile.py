"""Tests for project identity reconciliation, aliasing, and merging."""

from __future__ import annotations

import json

from amx.identity import resolve_project_id
from amx.identity.reconcile import find_duplicate_projects
from amx.memory.ingest import ingest_record
from amx.schema import RecordType
from amx.state.decision_log import record_decision
from amx.state.project_state import update_project_state
from amx.store import Store


def _seed(store, pid, *, title="thing", body="a body", entities=None, name=None, remote=None):
    store.ensure_project(pid, name=name, git_remote=remote)
    return ingest_record(store, pid, RecordType.THREAD, title, body, entities or [])


# Aliasing and canonical resolution tests.

def test_canonical_returns_raw_when_no_alias(store):
    assert store.canonical_project_id("name-foo") == "name-foo"


def test_alias_resolves_to_canonical(store):
    _seed(store, "name-foo")
    store.add_alias("path-abc", "name-foo")
    assert store.canonical_project_id("path-abc") == "name-foo"


def test_alias_target_is_flattened(store):
    """Verify aliasing to an aliased project resolves to the canonical project."""
    _seed(store, "name-foo")
    store.add_alias("git-1", "name-foo")
    store.add_alias("path-2", "git-1")
    assert store.canonical_project_id("path-2") == "name-foo"


def test_remove_alias(store):
    _seed(store, "name-foo")
    store.add_alias("path-abc", "name-foo")
    store.remove_alias("path-abc")
    assert store.canonical_project_id("path-abc") == "path-abc"


# Project merge tests.

def test_merge_reassigns_all_tables_and_aliases_source(store):
    _seed(store, "path-old", title="old work", body="from the path id")
    record_decision(store, "path-old", "Use SQLite", "local-first")
    update_project_state(store, "path-old", {"current_goal": "ship"})
    _seed(store, "name-proj", title="new work", body="from the name id")

    result = store.merge_projects("path-old", "name-proj")
    assert result["to_id"] == "name-proj"
    # Thread, state, and decision records should all move.
    assert result["moved"]["records"] >= 1
    assert result["moved"]["decisions"] == 1

    # Verify source ID resolves to target and data is moved.
    assert store.canonical_project_id("path-old") == "name-proj"
    titles = {r.title for r in store.recent_records("name-proj", 10)}
    assert {"old work", "new work"} <= titles
    assert any(d.title == "Use SQLite" for d in store.list_decisions("name-proj", 10))
    # Verify source project is empty.
    assert store.record_count("path-old") == 0


def test_merge_target_state_wins_source_preserved_in_audit(store):
    _seed(store, "path-old")
    update_project_state(store, "path-old", {"current_goal": "old goal"})
    _seed(store, "name-proj")
    update_project_state(store, "name-proj", {"current_goal": "new goal"})

    store.merge_projects("path-old", "name-proj")
    assert store.get_state("name-proj")["current_goal"] == "new goal"

    row = store._conn.execute(
        "SELECT moved_json FROM project_merges ORDER BY id DESC LIMIT 1"
    ).fetchone()

    audit = json.loads(row["moved_json"])
    assert audit["source_state"]["current_goal"] == "old goal"


def test_merge_logs_audit_with_moved_ids(store):
    rec = _seed(store, "path-old", title="x", body="y")
    _seed(store, "name-proj")
    store.merge_projects("path-old", "name-proj")

    row = store._conn.execute(
        "SELECT from_id, to_id, moved_json FROM project_merges ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["from_id"] == "path-old" and row["to_id"] == "name-proj"
    assert rec.id in json.loads(row["moved_json"])["records"]


def test_merge_into_self_rejected(store):
    _seed(store, "name-proj")
    try:
        store.merge_projects("name-proj", "name-proj")
        assert False, "expected ValueError"
    except ValueError:
        pass


# Duplicate detection tests.

def test_detects_same_remote_highest_confidence(store, cfg):
    _seed(store, "git-aaa", name="repo", remote="git@github.com:org/repo.git")
    _seed(store, "name-repo", name="repo", remote="https://github.com/org/repo.git")
    result = find_duplicate_projects(store, cfg)
    assert result["pairs"]
    top = result["pairs"][0]
    assert top["signal"] == "git_remote"
    assert top["confidence"] == 0.95
    assert {top["a"], top["b"]} == {"git-aaa", "name-repo"}


def test_distinct_hosts_not_flagged_by_remote(store, cfg):
    _seed(store, "git-a", name="repo", remote="git@github.com:org/repo.git")
    _seed(store, "git-b", name="other", remote="git@gitlab.com:org/repo.git")
    result = find_duplicate_projects(store, cfg)
    assert all(p["signal"] != "git_remote" for p in result["pairs"])


def test_no_duplicates_returns_note(store, cfg):
    _seed(store, "name-only-one")
    result = find_duplicate_projects(store, cfg)
    assert result["pairs"] == []
    assert result["note"] == "No likely duplicates found."


def test_merged_alias_excluded_from_detection(store, cfg):
    _seed(store, "git-aaa", name="repo", remote="git@github.com:org/repo.git")
    _seed(store, "name-repo", name="repo", remote="https://github.com/org/repo.git")
    store.merge_projects("git-aaa", "name-repo")
    # Aliased projects should be excluded from duplicate detection.
    result = find_duplicate_projects(store, cfg)
    assert result["pairs"] == []


# Backward compatibility tests.

def test_resolution_unchanged_without_aliases(store):
    """Verify canonicalization passes through when no aliases exist."""
    pid, _, _ = resolve_project_id(project_name="My Cool Project")
    assert store.canonical_project_id(pid) == "name-my-cool-project"
