"""Tests for Foundry IQ integration and grounding."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from amx.config import AMXConfig
from amx.integrations import foundry_iq
from amx.memory.ingest import ingest_record
from amx.memory.retrieval import search_memory
from amx.schema import RecordType


@pytest.fixture
def foundry_cfg(tmp_path) -> AMXConfig:
    return AMXConfig(
        db_path=tmp_path / "amx.db",
        foundry_endpoint="https://test.search.windows.net",
        foundry_api_key="test-key",
        foundry_index="test-index",
    )


def _fake_response(docs: list[dict]):
    body = json.dumps({"value": docs}).encode("utf-8")
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: body)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_env_file_loads_amx_vars_without_overriding(tmp_path, monkeypatch):
    from amx.config import _load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        'AMX_FOUNDRY_IQ_ENDPOINT="https://file.search.windows.net"\n'
        "AMX_FOUNDRY_IQ_API_KEY=file-key\n"
        "NOT_AMX_VAR=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AMX_FOUNDRY_IQ_ENDPOINT", raising=False)
    monkeypatch.setenv("AMX_FOUNDRY_IQ_API_KEY", "real-env-wins")

    _load_env_file(env_file)

    import os
    assert os.environ["AMX_FOUNDRY_IQ_ENDPOINT"] == "https://file.search.windows.net"
    assert os.environ["AMX_FOUNDRY_IQ_API_KEY"] == "real-env-wins"
    assert "NOT_AMX_VAR" not in os.environ
    monkeypatch.delenv("AMX_FOUNDRY_IQ_ENDPOINT", raising=False)


def test_search_skips_when_unconfigured(cfg):
    assert foundry_iq.search("anything", cfg) == []


def test_search_returns_matches(foundry_cfg):
    docs = [
        {"title": "Router firmware", "content": "OTA update flow.", "@search.score": 1.0},
        {"title": "Auth middleware", "content": "JWT refresh logic.", "@search.score": 0.5},
    ]
    with patch("urllib.request.urlopen", return_value=_fake_response(docs)):
        results = foundry_iq.search("firmware update", foundry_cfg)

    assert len(results) == 2
    assert all(m.source == "foundry_iq" for m in results)
    assert all(m.type == "grounded" for m in results)
    assert results[0].title == "Router firmware"
    # Ensure normalized score order matches raw score order.
    assert results[0].score > results[1].score


def test_search_falls_back_on_network_error(foundry_cfg):
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        results = foundry_iq.search("anything", foundry_cfg)
    assert results == []


def test_search_falls_back_on_timeout(foundry_cfg):
    with patch("urllib.request.urlopen", side_effect=TimeoutError()):
        results = foundry_iq.search("anything", foundry_cfg)
    assert results == []


def test_search_falls_back_on_bad_json(foundry_cfg):
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: b"not json")
    cm.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=cm):
        results = foundry_iq.search("anything", foundry_cfg)
    assert results == []


def test_retrieval_merges_foundry_results(store, foundry_cfg):
    ingest_record(store, "p1", RecordType.TASK, "Deploy firmware", "OTA update pending.")
    docs = [{"title": "Foundry: auth docs", "content": "JWT refresh.", "@search.score": 0.9}]
    with patch("urllib.request.urlopen", return_value=_fake_response(docs)):
        result = search_memory(store, "p1", "firmware auth", limit=10, cfg=foundry_cfg)

    sources = {m.source for m in result.matches}
    assert "local" in sources
    assert "foundry_iq" in sources
    scores = [m.score for m in result.matches]
    assert scores == sorted(scores, reverse=True)


def test_push_records_batches_and_counts(foundry_cfg):
    rows = [
        {"id": i, "project_id": "p1", "type": "task", "title": f"t{i}", "body": "b"}
        for i in range(150)
    ]
    calls = []

    def fake_urlopen(req, timeout=None):
        batch = json.loads(req.data)["value"]
        calls.append(len(batch))
        return _fake_response([{"status": True} for _ in batch])

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        accepted = foundry_iq.push_records(rows, foundry_cfg)

    assert accepted == 150
    assert calls == [100, 50]


def test_push_record_includes_project_and_type(foundry_cfg):
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data)["value"][0])
        return _fake_response([{"status": True}])

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        foundry_iq.push_record(7, "p1", "decision", "Use SQLite", "rationale", foundry_cfg)

    assert sent["id"] == "7"
    assert sent["project_id"] == "p1"
    assert sent["record_type"] == "decision"
    assert sent["title"] == "Use SQLite"


def test_push_record_is_silent_on_network_error(foundry_cfg):
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        # Ensure no exception is raised on network errors.
        foundry_iq.push_record(1, "p1", "task", "t", "b", foundry_cfg)


def test_ensure_index_creates_on_404(foundry_cfg):
    import urllib.error
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.get_method())
        if req.get_method() == "GET":
            raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, BytesIO(b""))
        return _fake_response([])

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert foundry_iq.ensure_index(foundry_cfg) is True

    assert calls == ["GET", "PUT"]


def test_search_prefixes_record_type_when_present(foundry_cfg):
    docs = [{"title": "Use SQLite", "record_type": "decision",
             "content": "x", "@search.score": 1.0}]
    with patch("urllib.request.urlopen", return_value=_fake_response(docs)):
        results = foundry_iq.search("sqlite", foundry_cfg)
    assert results[0].title == "[decision] Use SQLite"


def test_clear_all_deletes_every_doc(foundry_cfg):
    deleted = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data)
        if "search" in body:
            return _fake_response([{"id": "1"}, {"id": "2"}])
        deleted.extend(d["id"] for d in body["value"])
        return _fake_response([])

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert foundry_iq.clear_all(foundry_cfg) == 2
    # Track deleted document IDs.
    assert deleted == ["1", "2"]


def test_retrieval_is_local_only_when_foundry_offline(store, foundry_cfg):
    import urllib.error
    ingest_record(store, "p1", RecordType.TASK, "Deploy firmware", "OTA update pending.")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        result = search_memory(store, "p1", "firmware", limit=10, cfg=foundry_cfg)

    assert result.matches
    assert all(m.source == "local" for m in result.matches)
