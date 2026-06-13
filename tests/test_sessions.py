"""Tests for session continuity, chat summaries, and continuity digests."""

import json

import pytest

from amx.config import AMXConfig
from amx.mcp.server import create_server
from amx.memory.digest import build_continuity_digest
from amx.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "amx.db")


@pytest.fixture
def cfg(tmp_path):
    return AMXConfig(db_path=tmp_path / "amx.db")


@pytest.fixture
def server(tmp_path):
    return create_server(AMXConfig(db_path=tmp_path / "amx.db"))


def _payload(result):
    if isinstance(result, tuple):
        result = result[0]
    return json.loads(result[0].text)


# Store layer tests.

def test_session_summary_is_one_row_updated_in_place(store):
    first = store.upsert_session_summary("chat-1", "name-demo", "starting work")
    second = store.upsert_session_summary("chat-1", "name-demo", "now testing merge")
    assert first == second
    recent = store.recent_session_summaries(limit=10)
    assert len(recent) == 1
    assert recent[0].body == "now testing merge"


def test_chat_summary_does_not_pollute_project_summary(store):
    # Verify project summaries and session summaries coexist.
    store.insert_summary(
        __import__("amx.schema", fromlist=["Summary"]).Summary(
            project_id="name-demo", body="project-level summary"
        )
    )
    store.upsert_session_summary("chat-1", "name-demo", "tiny chat line")
    latest = store.latest_summary("name-demo")
    assert latest.body == "project-level summary"
    assert latest.session_id is None


def test_recent_session_summaries_newest_first(store):
    store.upsert_session_summary("chat-1", "p1", "older")
    store.upsert_session_summary("chat-2", "p2", "newer")
    bodies = [s.body for s in store.recent_session_summaries(limit=10)]
    assert bodies == ["newer", "older"]


def test_session_records_trail(store):
    from amx.memory.ingest import ingest_record
    from amx.schema import RecordType

    ingest_record(store, "name-demo", RecordType.TASK, "t1", "b1", session_id="chat-1")
    ingest_record(store, "name-demo", RecordType.THREAD, "t2", "b2", session_id="chat-1")
    ingest_record(store, "name-demo", RecordType.THREAD, "other", "b3", session_id="chat-2")
    trail = store.session_records("chat-1", limit=10)
    assert {r.title for r in trail} == {"t1", "t2"}


# Digest tests.

def test_digest_respects_budget(store, cfg):
    store.set_profile("Embedded engineer")
    # Verify budget limit truncates the digest list.
    for i in range(10):
        store.upsert_session_summary(f"chat-{i}", "p", f"working on feature number {i}")
    digest = build_continuity_digest(store, cfg, budget_tokens=12)
    assert digest["profile"] == "Embedded engineer"
    assert digest["used_tokens"] <= 12 or len(digest["recent_chats"]) == 1
    # Verify newest chats are ordered first.
    assert digest["recent_chats"][0]["session_id"] == "chat-9"


def test_digest_cold_start_empty(store, cfg):
    digest = build_continuity_digest(store, cfg)
    assert digest["profile"] is None
    assert digest["recent_chats"] == []


def test_digest_carries_capture_reminder(store, cfg):
    # Verify digest carries capture reminder.
    from amx.adoption import CAPTURE_REMINDER

    assert build_continuity_digest(store, cfg)["capture_reminder"] == CAPTURE_REMINDER


# MCP tool tests.

async def test_session_tools_registered(server):
    tools = {t.name for t in await server.list_tools()}
    assert {"memory_get_continuity_digest", "memory_get_session"} <= tools


async def test_submit_chat_summary_then_digest(server):
    await server.call_tool("memory_submit_summary", {
        "session_id": "chat-x", "body": "AMX Path B work", "project_name": "amx",
    })
    digest = _payload(await server.call_tool("memory_get_continuity_digest", {}))
    assert any(c["summary"] == "AMX Path B work" for c in digest["recent_chats"])


async def test_chat_summary_token_cap_enforced(server):
    with pytest.raises(Exception, match="cap"):
        await server.call_tool("memory_submit_summary", {
            "session_id": "chat-y",
            "body": " ".join(["word"] * 200),  # Content exceeding the token limit.
            "project_name": "amx",
        })


async def test_get_session_reloads_trail(server):
    await server.call_tool("memory_submit_summary", {
        "session_id": "chat-z", "body": "fixing the parser", "project_name": "amx",
    })
    await server.call_tool("memory_ingest", {
        "type": "bug", "title": "parser crash", "body": "off-by-one",
        "project_name": "amx", "session_id": "chat-z",
    })
    out = _payload(await server.call_tool("memory_get_session", {"session_id": "chat-z"}))
    assert out["summary"] == "fixing the parser"
    assert any(t["title"] == "parser crash" for t in out["trail"])
