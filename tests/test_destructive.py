"""Tests for data deletion, project purging, and database reset."""

import json

import pytest

from amx import cli
from amx.config import AMXConfig
from amx.mcp.server import create_server
from amx.memory.ingest import ingest_record
from amx.schema import RecordType
from amx.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "amx.db")


@pytest.fixture
def server(tmp_path):
    return create_server(AMXConfig(db_path=tmp_path / "amx.db"))


def _payload(result):
    if isinstance(result, tuple):
        result = result[0]
    return json.loads(result[0].text)


# Store layer tests.

def test_delete_records_removes_from_search(store):
    r = ingest_record(store, "name-p", RecordType.THREAD, "deletable", "body here")
    assert store.search_records("name-p", "deletable", 10)
    assert store.delete_records([r.id]) == 1
    assert store.get_record(r.id) is None
    assert store.search_records("name-p", "deletable", 10) == []


def test_delete_records_empty_is_noop(store):
    assert store.delete_records([]) == 0


def test_purge_project_clears_every_table(store):
    from amx.schema import Decision

    store.ensure_project("name-p", name="P")
    ingest_record(store, "name-p", RecordType.TASK, "t", "b")
    store.insert_decision(Decision(project_id="name-p", title="d", rationale="r"))
    store.set_state("name-p", {"current_goal": "x"})
    store.upsert_session_summary("s1", "name-p", "chat line")
    counts = store.purge_project("name-p")
    assert counts["records"] >= 1 and counts["decisions"] >= 1
    assert store.record_count("name-p") == 0
    assert store.get_state("name-p") is None
    assert store.get_project("name-p") is None


# MCP tool tests.

async def test_lookup_does_not_create(server):
    out = _payload(await server.call_tool("memory_lookup_project", {
        "project_name": "Ghost",
    }))
    assert out["exists"] is False
    # Project discovery should find nothing.
    found = _payload(await server.call_tool("memory_discover_projects", {}))
    assert all(c["project_id"] != "name-ghost" for c in found["candidates"])


async def test_lookup_reports_exists_after_write(server):
    await server.call_tool("memory_ingest", {
        "type": "task", "title": "x", "body": "y", "project_name": "Real",
    })
    out = _payload(await server.call_tool("memory_lookup_project", {
        "project_name": "Real",
    }))
    assert out["exists"] is True and out["record_count"] >= 1


async def test_delete_requires_confirm(server):
    ing = _payload(await server.call_tool("memory_ingest", {
        "type": "thread", "title": "x", "body": "y", "project_name": "P",
    }))
    with pytest.raises(Exception, match="confirm"):
        await server.call_tool("memory_delete", {"record_ids": [ing["record_id"]]})
    out = _payload(await server.call_tool("memory_delete", {
        "record_ids": [ing["record_id"]], "confirm": True,
    }))
    assert out["deleted"] == 1


async def test_purge_requires_confirm_and_real_project(server):
    await server.call_tool("memory_ingest", {
        "type": "task", "title": "x", "body": "y", "project_name": "Doomed",
    })
    with pytest.raises(Exception, match="confirm"):
        await server.call_tool("memory_purge_project", {"project_id": "name-doomed"})
    with pytest.raises(Exception, match="No project"):
        await server.call_tool("memory_purge_project", {
            "project_id": "name-nope", "confirm": True,
        })
    out = _payload(await server.call_tool("memory_purge_project", {
        "project_id": "name-doomed", "confirm": True,
    }))
    assert out["project_id"] == "name-doomed"


# CLI nukeit tests.

def test_nukeit_aborts_without_confirmation(tmp_path, monkeypatch, capsys):
    db = tmp_path / "amx.db"
    monkeypatch.setenv("AMX_DB_PATH", str(db))
    Store(db).close()
    assert db.exists()
    # Non-tty stdin aborts nukeit.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    rc = cli.main(["nukeit"])
    assert rc == 1 and db.exists()


def test_nukeit_yes_wipes_db(tmp_path, monkeypatch):
    db = tmp_path / "amx.db"
    monkeypatch.setenv("AMX_DB_PATH", str(db))
    Store(db).close()
    assert db.exists()
    rc = cli.main(["nukeit", "--yes"])
    assert rc == 0 and not db.exists()


def test_nukeit_handles_locked_db_without_crashing(tmp_path, monkeypatch):
    # Verify locked database handle is reported gracefully.
    db = tmp_path / "amx.db"
    monkeypatch.setenv("AMX_DB_PATH", str(db))
    holder = Store(db)
    try:
        rc = cli.main(["nukeit", "--yes"])
    finally:
        holder.close()
    assert rc in (0, 1)


def test_nukeit_no_db_is_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("AMX_DB_PATH", str(tmp_path / "amx.db"))
    assert cli.main(["nukeit", "--yes"]) == 0
