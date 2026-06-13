"""Tests for MCP server tool registration and routing."""

import json

import pytest

from amx.config import AMXConfig
from amx.mcp.server import create_server

@pytest.fixture
def server(tmp_path):
    return create_server(AMXConfig(db_path=tmp_path / "amx.db"))


def _payload(result):
    # Extract response payload from FastMCP return types.
    if isinstance(result, tuple):
        result = result[0]
    text = result[0].text
    return json.loads(text)


async def test_all_spec_tools_registered(server):
    tools = {t.name for t in await server.list_tools()}
    expected = {
        "memory_ingest", "memory_search", "memory_get_project_state",
        "memory_update_project_state", "memory_get_summary",
        "memory_submit_summary", "memory_get_context_bundle",
        "memory_record_decision", "memory_list_threads",
        "memory_get_profile", "memory_set_profile",
        "memory_discover_projects", "amx_init", "memory_checkpoint",
        "memory_update_status", "memory_supersede", "memory_correct",
        "memory_find_duplicates", "memory_merge_projects", "memory_alias_project",
    }
    assert expected <= tools


async def test_checkpoint_batches_captures(server):
    out = _payload(await server.call_tool("memory_checkpoint", {
        "project_name": "demo",
        "decisions": [{"title": "use SQLite", "rationale": "local-first"}],
        "records": [{"type": "task", "title": "ship rollback", "body": "do it"},
                    {"type": "bug", "title": "pipe hang", "body": "windows"}],
        "state": {"goal": "ship usage billing"},
        "summary": "AMX checkpoint demo",
    }))
    assert len(out["decisions"]) == 1
    assert len(out["records"]) == 2
    assert out["state"]["goal"] == "ship usage billing"
    assert out["summary"]["summary_id"]

    # Verify all data lands in the target project.
    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_name": "demo",
    }))
    titled = {s["title"] for s in bundle["slices"]}
    assert "ship rollback" in titled and "use SQLite" in titled


async def test_update_status_closes_task_in_bundle(server):
    ingested = _payload(await server.call_tool("memory_ingest", {
        "type": "task", "title": "ship rollback", "body": "do it",
        "project_name": "demo",
    }))
    await server.call_tool("memory_update_status", {
        "record_id": ingested["record_id"], "status": "done",
    })
    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_name": "demo",
    }))
    assert all(s["title"] != "ship rollback" for s in bundle["slices"])


async def test_update_status_rejects_non_lifecycle_record(server):
    ingested = _payload(await server.call_tool("memory_ingest", {
        "type": "thread", "title": "note", "body": "x", "project_name": "demo",
    }))
    with pytest.raises(Exception, match="task/bug"):
        await server.call_tool("memory_update_status", {
            "record_id": ingested["record_id"], "status": "done",
        })


async def test_correct_replaces_in_bundle(server):
    old = _payload(await server.call_tool("memory_ingest", {
        "type": "task", "title": "rollback pending", "body": "not shipped yet",
        "project_name": "demo",
    }))
    await server.call_tool("memory_correct", {
        "record_id": old["record_id"],
        "title": "rollback shipped",
        "body": "shipped last week",
    })
    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_name": "demo",
    }))
    titles = [s["title"] for s in bundle["slices"]]
    assert "rollback shipped" in titles
    assert "rollback pending" not in titles


async def test_find_duplicates_then_merge_unifies_bundle(server):
    # Verify merging resolves projects across different ID strategies.
    for ident in ({"project_id": "git-rtr"}, {"project_name": "router-tool"}):
        await server.call_tool("memory_ingest", {
            "type": "thread", "title": "shared design",
            "body": "ota firmware design doc", **ident,
        })
    a = _payload(await server.call_tool("memory_ingest", {
        "type": "thread", "title": "first half",
        "body": "ota firmware update flow", "project_id": "git-rtr",
    }))
    await server.call_tool("memory_ingest", {
        "type": "thread", "title": "second half",
        "body": "ota firmware rollback flow", "project_name": "router-tool",
    })

    found = _payload(await server.call_tool("memory_find_duplicates", {}))
    assert found["pairs"]
    assert {found["pairs"][0]["a"], found["pairs"][0]["b"]} == {"git-rtr", "name-router-tool"}

    merged = _payload(await server.call_tool("memory_merge_projects", {
        "from_project_id": "git-rtr", "to_project_id": "name-router-tool",
    }))
    assert merged["to_id"] == "name-router-tool"

    # Verify both IDs now resolve to the merged project.
    for ident in ({"project_id": "git-rtr"}, {"project_name": "router-tool"}):
        bundle = _payload(await server.call_tool("memory_get_context_bundle", ident))
        assert bundle["project_id"] == "name-router-tool"
        titles = [s["title"] for s in bundle["slices"]]
        assert "first half" in titles and "second half" in titles
    assert a["project_id"] == "git-rtr"


async def test_alias_project_redirects_resolution(server):
    await server.call_tool("memory_ingest", {
        "type": "thread", "title": "kept", "body": "memory", "project_name": "keep",
    })
    aliased = _payload(await server.call_tool("memory_alias_project", {
        "alias": "path-moved", "project_id": "name-keep",
    }))
    assert aliased["project_id"] == "name-keep"
    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_id": "path-moved",
    }))
    assert bundle["project_id"] == "name-keep"


async def test_amx_init_roundtrip_and_idempotency(server):
    cold = _payload(await server.call_tool("amx_init", {}))
    assert cold["status"]["initialized"] is False
    assert cold["instruction"]

    applied = _payload(await server.call_tool("amx_init", {"applied": True}))
    assert applied["recorded"] is True
    assert applied["status"]["initialized"] is True

    # Verify re-init is a no-op when current.
    again = _payload(await server.call_tool("amx_init", {}))
    assert again["status"]["already_current"] is True
    assert again["recorded"] is False


async def test_tool_calls_are_logged(server, tmp_path):
    from amx.store import Store

    await server.call_tool("amx_init", {})
    await server.call_tool("memory_ingest", {
        "type": "task", "title": "t", "body": "b", "project_name": "demo",
    })
    counts = Store(tmp_path / "amx.db").tool_call_counts()
    assert counts.get("amx_init", 0) >= 1
    assert counts.get("memory_ingest", 0) >= 1


async def test_discover_then_load_roundtrip(server):
    # Verify discovery works end-to-end.
    await server.call_tool("memory_ingest", {
        "type": "thread", "title": "YouTube downloader",
        "body": "fetch and save youtube videos", "project_name": "grabber",
    })
    await server.call_tool("memory_ingest", {
        "type": "thread", "title": "Router firmware",
        "body": "ota update flow", "project_name": "router-tool",
    })

    found = _payload(await server.call_tool("memory_discover_projects", {
        "description": "youtube downloader",
    }))
    assert found["candidates"]
    top = found["candidates"][0]
    assert top["project_id"] == "name-grabber"

    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_id": top["project_id"],
    }))
    assert bundle["project_id"] == "name-grabber"


async def test_discover_empty_store_graceful(server):
    found = _payload(await server.call_tool("memory_discover_projects", {
        "description": "nothing here",
    }))
    assert found["candidates"] == []
    assert found["note"] == "No projects found."


async def test_ingest_dedup_visible_through_tool(server):
    first = _payload(await server.call_tool("memory_ingest", {
        "type": "bug", "title": "OTA hangs", "body": "pipe closes on Windows",
        "project_name": "router-tool",
    }))
    assert first["deduped"] is False
    second = _payload(await server.call_tool("memory_ingest", {
        "type": "bug", "title": "OTA hangs", "body": "pipe closes on Windows",
        "project_name": "router-tool",
    }))
    assert second["deduped"] is True
    assert second["record_id"] == first["record_id"]


async def test_decision_not_double_counted_in_bundle(server):
    await server.call_tool("memory_record_decision", {
        "title": "Use SQLite", "rationale": "Local-first storage.",
        "project_name": "amx-demo",
    })
    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_name": "amx-demo",
    }))
    titled = [s for s in bundle["slices"] if s["title"] == "Use SQLite"]
    assert len(titled) == 1
    assert titled[0]["kind"] == "decision"


async def test_profile_set_get_roundtrip(server):
    result = _payload(await server.call_tool("memory_set_profile", {
        "text": "Embedded dev; prefers Rust; building AMX.",
    }))
    assert result["ok"] is True

    got = _payload(await server.call_tool("memory_get_profile", {}))
    assert got["profile"] == "Embedded dev; prefers Rust; building AMX."


async def test_profile_oversized_write_rejected(server):
    with pytest.raises(Exception, match="cap"):
        await server.call_tool("memory_set_profile", {"text": "word " * 500})

    # Verify write was not persisted.
    got = _payload(await server.call_tool("memory_get_profile", {}))
    assert got["profile"] is None


async def test_profile_empty_write_clears(server):
    await server.call_tool("memory_set_profile", {"text": "Something."})
    cleared = _payload(await server.call_tool("memory_set_profile", {"text": ""}))
    assert cleared["cleared"] is True
    got = _payload(await server.call_tool("memory_get_profile", {}))
    assert got["profile"] is None


async def test_bundle_includes_profile_first(server):
    await server.call_tool("memory_set_profile", {"text": "Prefers Rust."})
    await server.call_tool("memory_update_project_state", {
        "patch": {"current_goal": "Ship Phase 1"},
        "project_name": "amx-demo",
    })
    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_name": "amx-demo",
        "budget_tokens": 1000,
    }))
    assert bundle["slices"][0]["kind"] == "user_profile"
    assert bundle["used_tokens"] <= 1000


async def test_ingest_then_search_roundtrip(server):
    ingest = _payload(await server.call_tool("memory_ingest", {
        "type": "decision",
        "title": "Use SQLite for metadata",
        "body": "Local-first storage choice.",
        "project_name": "amx-demo",
    }))
    assert ingest["record_id"] is not None
    assert ingest["project_id"] == "name-amx-demo"

    search = _payload(await server.call_tool("memory_search", {
        "query": "sqlite metadata",
        "project_name": "amx-demo",
    }))
    assert search["matches"]
    assert search["matches"][0]["title"] == "Use SQLite for metadata"


async def test_cross_session_handoff(server):
    # Verify project state handoff between different client calls.
    await server.call_tool("memory_update_project_state", {
        "patch": {"current_goal": "Ship Phase 1", "active_task": "Demo video"},
        "project_name": "amx-demo",
    })
    bundle = _payload(await server.call_tool("memory_get_context_bundle", {
        "project_name": "amx-demo",
        "budget_tokens": 1000,
    }))
    assert bundle["cold_start"] is False
    state_slice = next(s for s in bundle["slices"] if s["kind"] == "project_state")
    assert state_slice["content"]["current_goal"] == "Ship Phase 1"
    assert bundle["used_tokens"] <= 1000
