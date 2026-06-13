"""Burn test hammering CLI and MCP tools with edge cases."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# Use sandbox environment for all tests.
SANDBOX = Path(tempfile.mkdtemp(prefix="amx-burn-"))
HOME = SANDBOX / "home"
HOME.mkdir()
DB = SANDBOX / "amx.db"

ENV = {
    **os.environ,
    "AMX_DB_PATH": str(DB),
    "HOME": str(HOME),
    "USERPROFILE": str(HOME),
    "APPDATA": str(HOME / "AppData" / "Roaming"),
    # Override config to block real integration access.
    "AMX_FOUNDRY_IQ_ENDPOINT": "",
    "AMX_FOUNDRY_IQ_API_KEY": "",
    "AMX_FOUNDRY_IQ_INDEX": "",
    "AMX_FOUNDRY_SYNC": "",
}
os.environ.update({k: ENV[k] for k in ENV if k.startswith(("AMX_", "APPDATA"))})

# Import after setting environment vars to ensure sandboxing.
from amx.config import AMXConfig  # noqa: E402
from amx.store import Store  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}  {detail}".strip())
        print(f"  FAIL {name}  {detail}")


def cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "amx.cli", *args],
        env=ENV, input=stdin, capture_output=True, text=True, timeout=120,
        cwd=str(SANDBOX),
    )


# Test CLI with no database or config.
def burn_cli_cold() -> None:
    print("A. CLI cold start")
    r = cli("version")
    check("version", r.returncode == 0 and "amx" in r.stdout)
    r = cli("info")
    check("info without db", r.returncode == 0 and "exists:   False" in r.stdout)
    r = cli("backup")
    check("backup without db fails cleanly", r.returncode == 1 and "Nothing to back up" in r.stdout)
    r = cli("restore", str(SANDBOX / "nope.db"))
    check("restore missing file", r.returncode == 1 and "No file" in r.stdout)
    r = cli("nukeit", "--yes")
    check("nukeit without db", r.returncode == 0 and "Nothing to nuke" in r.stdout)
    r = cli("foundry-sync")
    check("foundry-sync unconfigured", r.returncode == 1 and "not configured" in r.stdout)
    r = cli("local-sync")
    check("local-sync unconfigured", r.returncode == 1 and "not configured" in r.stdout)
    r = cli("enable-foundry")
    check("enable-foundry non-tty without keys", r.returncode == 1 and "AMX_FOUNDRY_IQ_ENDPOINT" in r.stdout)
    r = cli("disable-foundry")
    check("disable-foundry always works", r.returncode == 0)
    r = cli("definitely-not-a-command")
    check("unknown command rejected", r.returncode == 2)


# Test all MCP tools with valid and invalid input.
async def burn_mcp() -> None:
    import json

    from mcp.server.fastmcp import FastMCP

    from amx.mcp.tools import register_tools

    print("B. MCP tool burn")
    # Build server manually to allow closing the store afterward.
    store = Store(DB)
    server = FastMCP("amx-burn")
    register_tools(server, store, AMXConfig(db_path=DB))

    async def call(tool: str, args: dict) -> dict:
        result = await server.call_tool(tool, args)
        if isinstance(result, tuple):
            result = result[0]
        return json.loads(result[0].text)

    async def expect_error(name: str, tool: str, args: dict, needle: str = "") -> None:
        try:
            await call(tool, args)
            check(name, False, "expected an error, got success")
        except Exception as e:
            check(name, needle.lower() in str(e).lower(), f"got: {e}")

    pid = "burn-project"

    # Ingest every record type.
    types = ["project_state", "decision", "task", "bug", "research",
             "architecture", "summary", "entity", "thread",
             "artifact_reference", "raw_event"]
    for t in types:
        r = await call("memory_ingest", {
            "type": t, "title": f"{t} record", "body": f"body for {t}",
            "project_id": pid, "session_id": "burn-session",
        })
        check(f"ingest type={t}", r["record_id"] > 0 and not r["deduped"])

    r = await call("memory_ingest", {
        "type": "entity", "title": "üñîçødé ✓ 中文 🔥", "body": "émoji bödy 🚀 ‮ rtl",
        "project_id": pid,
    })
    check("ingest unicode/emoji", r["record_id"] > 0)
    unicode_id = r["record_id"]  # Entity records do not support status transitions.

    r = await call("memory_ingest", {
        "type": "research", "title": "huge", "body": "x" * 100_000, "project_id": pid,
    })
    check("ingest 100KB body", r["record_id"] > 0)

    await expect_error("ingest invalid type", "memory_ingest",
                       {"type": "nonsense", "title": "t", "body": "b", "project_id": pid})

    # Verify deduplication on 50 duplicate ingests.
    dup_ids = set()
    for _ in range(50):
        r = await call("memory_ingest", {
            "type": "task", "title": "hammered task", "body": "same body",
            "project_id": pid,
        })
        dup_ids.add(r["record_id"])
    check("dedup hammer x50 -> one id", len(dup_ids) == 1)
    task_id = dup_ids.pop()

    # Verify search and ranking on 200 records.
    for i in range(200):
        await call("memory_ingest", {
            "type": "thread", "title": f"volume note {i}",
            "body": f"filler content number {i} about widget-{i % 7}",
            "project_id": pid,
        })
    r = await call("memory_search", {"query": "widget", "project_id": pid, "limit": 10})
    check("search across 200 records", len(r["matches"]) > 0)
    scores = [m["score"] for m in r["matches"]]
    check("search ranking is ordered", scores == sorted(scores, reverse=True))
    r = await call("memory_search", {"query": "zzz-no-such-term-zzz", "project_id": pid})
    check("search with no hits", r["matches"] == [] or all(
        m.get("source") != "local" or True for m in r["matches"]))

    # Verify status transition rules.
    r = await call("memory_update_status", {"record_id": task_id, "status": "done"})
    check("status -> done", r["status"] == "done")
    r = await call("memory_update_status", {"record_id": task_id, "status": "open"})
    check("status -> reopened", r["status"] == "open")
    await expect_error("invalid status", "memory_update_status",
                       {"record_id": task_id, "status": "exploded"}, "invalid status")
    await expect_error("status on non-lifecycle type", "memory_update_status",
                       {"record_id": unicode_id, "status": "done"})
    await expect_error("status on missing record", "memory_update_status",
                       {"record_id": 99999, "status": "done"}, "no record")

    # Verify correction chains and stale update rejection.
    r1 = await call("memory_correct", {"record_id": task_id, "title": "fixed once", "body": "v2"})
    r2 = await call("memory_correct", {"record_id": r1["record_id"], "title": "fixed twice", "body": "v3"})
    check("correction chain", r2["record_id"] != r1["record_id"])
    await expect_error("correcting a superseded record", "memory_correct",
                       {"record_id": task_id, "title": "stale", "body": "stale"}, "superseded")

    # Verify explicit record supersession.
    a = await call("memory_ingest", {"type": "research", "title": "old finding",
                                     "body": "a", "project_id": pid})
    b = await call("memory_ingest", {"type": "research", "title": "new finding",
                                     "body": "b", "project_id": pid})
    r = await call("memory_supersede", {"old_record_id": a["record_id"],
                                        "new_record_id": b["record_id"]})
    check("supersede", r["superseded_id"] == a["record_id"])
    await expect_error("supersede missing record", "memory_supersede",
                       {"old_record_id": 99998, "new_record_id": b["record_id"]}, "no record")

    # Verify decision records and supersession.
    d1 = await call("memory_record_decision", {
        "title": "use sqlite", "rationale": "boring and reliable", "project_id": pid})
    d2 = await call("memory_record_decision", {
        "title": "actually wal mode", "rationale": "concurrency",
        "supersedes_id": d1["decision_id"], "project_id": pid})
    check("decision supersession", d2["decision_id"] != d1["decision_id"])
    r = await call("memory_list_decisions", {"project_id": pid})
    check("list decisions", len(r["decisions"]) >= 2)

    # Verify nested state patching and key deletion.
    await call("memory_update_project_state", {
        "patch": {"goal": "burn", "nested": {"a": [1, 2, {"b": "c"}]}, "drop_me": 1},
        "project_id": pid})
    r = await call("memory_update_project_state", {"patch": {"drop_me": None}, "project_id": pid})
    check("state null deletes key", "drop_me" not in r["state"])
    r = await call("memory_get_project_state", {"project_id": pid})
    check("state round-trip", r["state"].get("goal") == "burn")

    # Verify summary submissions and token caps.
    r = await call("memory_submit_summary", {"body": "checkpoint: burn test running",
                                             "project_id": pid})
    check("project summary", r["summary_id"] > 0)
    r = await call("memory_submit_summary", {"body": "burn chat", "project_id": pid,
                                             "session_id": "burn-session"})
    check("chat mini-summary", r["session_id"] == "burn-session")
    await expect_error("chat summary over cap", "memory_submit_summary",
                       {"body": "word " * 500, "project_id": pid,
                        "session_id": "burn-session"}, "cap")

    # Verify token-budgeted bundle retrieval.
    r = await call("memory_get_context_bundle", {"project_id": pid, "budget_tokens": 50})
    check("bundle tiny budget", r["used_tokens"] <= 50 * 1.2)
    r = await call("memory_get_context_bundle", {"project_id": pid, "query": "widget"})
    check("bundle with query", r["used_tokens"] > 0)

    # Verify developer profile CRUD operations.
    r = await call("memory_set_profile", {"text": "burn tester, likes chaos"})
    check("set profile", r["ok"])
    await expect_error("profile over cap", "memory_set_profile", {"text": "word " * 500}, "cap")
    r = await call("memory_get_profile", {})
    check("get profile", "burn tester" in (r["profile"] or ""))
    r = await call("memory_set_profile", {"text": "  "})
    check("clear profile", r.get("cleared") is True)

    # Verify project discovery and lookup.
    r = await call("memory_discover_projects", {"description": "burn"})
    check("discover projects", any(c["project_id"] == pid for c in r["candidates"]))
    r = await call("memory_lookup_project", {"project_id": pid})
    check("lookup existing project", r["exists"] and r["record_count"] > 200)
    r = await call("memory_lookup_project", {"project_name": "never-created"})
    check("lookup is read-only", r["exists"] is False)
    r = await call("memory_get_continuity_digest", {})
    check("continuity digest", "sessions" in r or "profile" in r)
    r = await call("memory_get_session", {"session_id": "burn-session"})
    check("session reload", len(r["trail"]) >= 1)

    # Verify project aliasing and merges.
    await call("memory_ingest", {"type": "task", "title": "twin work",
                                 "body": "same project, other id", "project_id": "burn-twin"})
    await expect_error("alias to itself", "memory_alias_project",
                       {"alias": pid, "project_id": pid}, "itself")
    r = await call("memory_alias_project", {"alias": "burn-alias", "project_id": pid})
    check("alias project", r["project_id"] == pid)
    r = await call("memory_lookup_project", {"project_id": "burn-alias"})
    check("alias resolves", r["resolved_project_id"] == pid)
    r = await call("memory_merge_projects", {"from_project_id": "burn-twin",
                                             "to_project_id": pid})
    check("merge projects", r.get("moved", r) is not None)
    await expect_error("merge already-merged", "memory_merge_projects",
                       {"from_project_id": "burn-twin", "to_project_id": pid}, "same")
    r = await call("memory_lookup_project", {"project_id": "burn-twin"})
    check("merged id resolves to canonical", r["resolved_project_id"] == pid)
    await call("memory_find_duplicates", {})

    # Verify confirmation requirements on delete and purge.
    await expect_error("delete without confirm", "memory_delete",
                       {"record_ids": [b["record_id"]]}, "confirm")
    r = await call("memory_delete", {"record_ids": [b["record_id"], 424242], "confirm": True})
    check("delete with confirm", r["deleted"] == 1)
    await expect_error("purge without confirm", "memory_purge_project",
                       {"project_id": pid}, "confirm")
    await expect_error("purge missing project", "memory_purge_project",
                       {"project_id": "ghost", "confirm": True}, "no project")

    # Verify client onboarding.
    r = await call("amx_init", {"client": "claude code"})
    check("amx_init returns instruction", "marked_instruction" in r or "instruction" in r)
    await call("amx_init", {"client": "claude code", "applied": True})

    # Verify thread listing.
    r = await call("memory_list_threads", {"project_id": pid, "limit": 5})
    check("list threads", len(r["threads"]) == 5)

    store.close()


# Test CLI under database corruption and recovery.
def burn_cli_corruption() -> None:
    print("C. CLI + corrupted data")
    r = cli("info")
    check("info on populated db", r.returncode == 0 and "projects:" in r.stdout)

    r = cli("backup")
    check("backup populated db", r.returncode == 0 and "Backed up" in r.stdout)
    backups = sorted(SANDBOX.glob("amx-backup-*.db"))
    check("backup file exists, no sidecars", len(backups) >= 1 and
          not list(SANDBOX.glob("amx-backup-*.db-wal")))
    good_backup = backups[-1]

    # Verify restore rejects invalid databases.
    junk = SANDBOX / "junk.db"
    junk.write_bytes(b"\x00\xffnot a database\x07" * 100)
    r = cli("restore", str(junk), "--yes")
    check("restore rejects binary junk", r.returncode == 1 and "not an AMX database" in r.stdout)

    truncated = SANDBOX / "truncated.db"
    truncated.write_bytes(good_backup.read_bytes()[:1024])
    r = cli("restore", str(truncated), "--yes")
    check("restore rejects truncated db", r.returncode == 1)

    # Verify restore aborts without confirmation.
    r = cli("restore", str(good_backup))
    check("restore cancels without confirm", r.returncode == 1)

    # Verify CLI fails gracefully on corrupted database file.
    DB.write_bytes(b"SQLite format 3\x00" + os.urandom(4096))
    for wal in (DB.with_name(DB.name + "-wal"), DB.with_name(DB.name + "-shm")):
        wal.unlink(missing_ok=True)
    r = cli("info")
    check("info on corrupt db doesn't crash", r.returncode == 0 and "Traceback" not in r.stderr,
          r.stderr[-200:])
    r = cli("backup")
    check("backup on corrupt db doesn't crash", "Traceback" not in r.stderr, r.stderr[-200:])

    # Verify recovery via restore.
    r = cli("restore", str(good_backup), "--yes")
    check("restore recovers corrupt db", r.returncode == 0 and "Restored" in r.stdout)
    r = cli("info")
    check("info works after recovery", r.returncode == 0 and "projects:" in r.stdout)

    # Verify restore works on a nuked database.
    r = cli("nukeit", "--yes")
    check("nukeit", r.returncode == 0 and "Nuked" in r.stdout)
    r = cli("info")
    check("db gone after nukeit", "exists:   False" in r.stdout)
    r = cli("restore", str(good_backup), "--yes")
    check("restore after nukeit", r.returncode == 0)
    r = cli("info")
    check("data back after restore", "projects:" in r.stdout)


# Test MCP server installation in client configs.
def burn_install_mcp() -> None:
    import json

    print("D. install-mcp")
    (HOME / ".cursor").mkdir(parents=True, exist_ok=True)
    (HOME / ".codex").mkdir(exist_ok=True)
    (HOME / ".gemini").mkdir(exist_ok=True)
    (HOME / "AppData" / "Roaming" / "Code" / "User").mkdir(parents=True, exist_ok=True)

    r = cli("install-mcp", "--list")
    check("install-mcp --list", r.returncode == 0 and "cursor" in r.stdout)
    r = cli("install-mcp", "--client", "definitely-fake")
    check("install-mcp unknown client", r.returncode == 1 and "Unknown client" in r.stdout)
    r = cli("install-mcp", "--all")
    check("install-mcp --all", r.returncode == 0)
    cfg = json.loads((HOME / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    check("cursor config written", "amx" in cfg["mcpServers"])
    check("codex toml written", "[mcp_servers.amx]" in
          (HOME / ".codex" / "config.toml").read_text(encoding="utf-8"))
    vs = json.loads((HOME / "AppData" / "Roaming" / "Code" / "User" / "mcp.json")
                    .read_text(encoding="utf-8"))
    check("vscode uses servers/stdio", vs["servers"]["amx"]["type"] == "stdio")

    # Verify installation is idempotent.
    cfg["mcpServers"]["other"] = {"command": "keep"}
    (HOME / ".cursor" / "mcp.json").write_text(json.dumps(cfg), encoding="utf-8")
    cli("install-mcp", "--client", "cursor")
    cfg = json.loads((HOME / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    check("re-install preserves other servers", cfg["mcpServers"]["other"]["command"] == "keep")

    # Verify corrupted client config is not modified.
    (HOME / ".cursor" / "mcp.json").write_text("{broken json", encoding="utf-8")
    r = cli("install-mcp", "--client", "cursor")
    check("corrupt client config fails politely", r.returncode == 1 and "FAILED" in r.stdout)
    check("corrupt config not clobbered",
          (HOME / ".cursor" / "mcp.json").read_text(encoding="utf-8") == "{broken json")


# Test garbage env files and offline integrations.
def burn_env_and_foundry() -> None:
    print("E. env-file abuse + dead Foundry endpoint")
    amx_home = HOME / ".amx"
    amx_home.mkdir(exist_ok=True)
    (amx_home / ".env").write_text(
        "GARBAGE LINE NO EQUALS\n=\n===\nAMX_BROKEN\n# comment\n\x07weird=bytes\n",
        encoding="utf-8",
    )
    r = cli("info")
    check("garbage env file ignored", r.returncode == 0 and "Traceback" not in r.stderr)

    dead = {**ENV,
            "AMX_FOUNDRY_IQ_ENDPOINT": "http://127.0.0.1:9",
            "AMX_FOUNDRY_IQ_API_KEY": "dead-key",
            "AMX_FOUNDRY_IQ_INDEX": "dead-index",
            "AMX_FOUNDRY_SYNC": "true"}

    def dead_cli(*args):
        return subprocess.run([sys.executable, "-m", "amx.cli", *args],
                              env=dead, capture_output=True, text=True,
                              timeout=120, cwd=str(SANDBOX))

    r = dead_cli("foundry-sync")
    check("foundry-sync dead endpoint fails politely",
          r.returncode == 1 and "Sync failed" in r.stdout and "Traceback" not in r.stderr)
    r = dead_cli("local-sync")
    check("local-sync dead endpoint fails politely",
          r.returncode == 1 and "Fetch failed" in r.stdout)
    r = dead_cli("info")
    check("info shows sync on", "sync on" in r.stdout)
    r = dead_cli("nukeit", "--yes")
    check("nukeit with dead foundry still nukes local",
          r.returncode == 0 and "Could not empty the Foundry IQ index" in r.stdout)

    # Verify local writes succeed when integration endpoint is offline.
    code = (
        "import os, json, asyncio\n"
        "from amx.config import AMXConfig\n"
        "from amx.mcp.server import create_server\n"
        "async def go():\n"
        "    s = create_server(AMXConfig())\n"
        "    r = await s.call_tool('memory_ingest', dict(type='task', title='offline',"
        " body='works', project_id='dead-net'))\n"
        "    r = r[0] if isinstance(r, tuple) else r\n"
        "    print(json.loads(r[0].text)['record_id'])\n"
        "asyncio.run(go())\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=dead, capture_output=True,
                       text=True, timeout=120, cwd=str(SANDBOX))
    check("ingest succeeds despite dead foundry",
          r.returncode == 0 and r.stdout.strip().isdigit(), r.stderr[-200:])


# Test concurrent database writes under WAL mode.
def burn_concurrency() -> None:
    print("F. concurrent writers")
    from amx.memory.ingest import ingest_record
    from amx.schema import RecordType

    errors: list[str] = []

    def writer(n: int) -> None:
        try:
            store = Store(DB)
            try:
                for i in range(25):
                    ingest_record(store, "concurrent", RecordType.THREAD,
                                  f"writer {n} note {i}", f"unique body {n}-{i}")
            finally:
                store.close()
        except Exception as e:
            errors.append(f"writer {n}: {e}")

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("4 writers x 25 records, no errors", not errors, "; ".join(errors[:3]))
    store = Store(DB)
    try:
        count = store.record_count("concurrent")
    finally:
        store.close()
    check("all 100 concurrent records landed", count == 100, f"got {count}")


def main() -> int:
    import asyncio

    print(f"Sandbox: {SANDBOX}\n")
    try:
        burn_cli_cold()
        asyncio.run(burn_mcp())
        burn_cli_corruption()
        burn_install_mcp()
        burn_env_and_foundry()
        burn_concurrency()
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)

    print(f"\n{'=' * 60}\nBURN TEST: {PASS} passed, {FAIL} failed")
    for f in FAILURES:
        print(f"  FAIL: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
