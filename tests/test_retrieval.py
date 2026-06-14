"""Tests for ranked memory retrieval and search determinism."""

from __future__ import annotations

from amx.memory.ingest import ingest_record
from amx.memory.retrieval import search_memory
from amx.schema import RecordType


def _seed(store):
    ingest_record(store, "p1", RecordType.DECISION, "Use SQLite for metadata",
                  "Chose SQLite over Postgres for local-first storage.")
    ingest_record(store, "p1", RecordType.TASK, "Implement ranked search",
                  "Search must return ordered results with scores.")
    ingest_record(store, "p1", RecordType.RAW_EVENT, "search command run",
                  "User ran a search in the CLI.")


def test_search_returns_ranked_matches(store, cfg):
    _seed(store)
    result = search_memory(store, "p1", "search ranking", limit=10, cfg=cfg)
    assert result.matches
    scores = [m.score for m in result.matches]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_search_is_deterministic(store, cfg):
    _seed(store)
    a = search_memory(store, "p1", "search", limit=10, cfg=cfg)
    b = search_memory(store, "p1", "search", limit=10, cfg=cfg)
    assert [m.record_id for m in a.matches] == [m.record_id for m in b.matches]


def test_stemming_matches_word_variants(store, cfg):
    _seed(store)
    # "Implement ranked search" / "ranked search" should be found by variants.
    assert search_memory(store, "p1", "searching", limit=10, cfg=cfg).matches
    assert search_memory(store, "p1", "ranking results", limit=10, cfg=cfg).matches
    # A task record titled with "search" is found when querying plural forms.
    titles = [m.title for m in search_memory(store, "p1", "searches", limit=10, cfg=cfg).matches]
    assert any("search" in t.lower() for t in titles)


def test_no_matches_for_unrelated_query(store, cfg):
    _seed(store)
    result = search_memory(store, "p1", "zebra spaceship", limit=10, cfg=cfg)
    assert result.matches == []
