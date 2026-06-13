"""Smoke test script for verifying FastMCP server tools over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), str(detail)[:160]))


class ToolError(Exception):
    pass


def parse(res):
    if getattr(res, "isError", False):
        raise ToolError(res.content[0].text if res.content else "error")
    if res.content and getattr(res.content[0], "text", None) is not None:
        try:
            return json.loads(res.content[0].text)
        except Exception:
            return res.content[0].text
    return None


async def call(s, name, **args):
    return parse(await s.call_tool(name, args))


async def expect_error(s, name, tool, **args):
    try:
        await call(s, tool, **args)
        check(name, False, "expected an error but call succeeded")
    except ToolError as e:
        check(name, True, str(e))


async def run(db_path: str) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "amx.mcp.server"],
        env={**os.environ, "AMX_DB_PATH": db_path, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            tools = {t.name for t in (await s.list_tools()).tools}
            expected = {
                "amx_init", "memory_get_profile", "memory_set_profile",
                "memory_discover_projects", "memory_get_continuity_digest",
                "memory_lookup_project", "memory_get_session", "memory_ingest",
                "memory_search", "memory_get_project_state",
                "memory_update_project_state", "memory_get_summary",
                "memory_submit_summary", "memory_get_context_bundle",
                "memory_record_decision", "memory_list_threads",
                "memory_list_decisions", "memory_update_status", "memory_supersede",
                "memory_find_duplicates", "memory_merge_projects",
                "memory_alias_project", "memory_correct", "memory_delete",
                "memory_purge_project",
            }
            check("all tools registered", expected <= tools, sorted(expected - tools))


            init = await call(s, "amx_init", client="smoke", applied=True)
            check("amx_init onboards", init["status"]["initialized"], init["status"])


            await call(s, "memory_set_profile", text="Smoke tester, local-first")
            prof = await call(s, "memory_get_profile")
            check("profile round-trips", prof["profile"].startswith("Smoke tester"), prof)


            dig0 = await call(s, "memory_get_continuity_digest")
            check("digest returns profile", dig0["profile"] is not None, dig0["recent_chats"])


            look = await call(s, "memory_lookup_project", project_name="Smoke Proj")
            check("lookup reports not-exists, no create", look["exists"] is False, look)

            # Ingest a task.
            ing = await call(s, "memory_ingest", type="task", title="wire smoke",
                             body="exercise all tools", project_name="Smoke Proj",
                             session_id="sess-1")
            check("ingest task", ing["record_id"] is not None, ing)
            await call(s, "memory_update_project_state", project_name="Smoke Proj",
                       patch={"current_goal": "ship", "active_task": "smoke"})
            st = await call(s, "memory_get_project_state", project_name="Smoke Proj")
            check("state set", st["state"]["current_goal"] == "ship", st["state"])
            dec = await call(s, "memory_record_decision", project_name="Smoke Proj",
                             title="use sqlite", rationale="local-first")
            check("record decision", dec["decision_id"] is not None, dec)


            look2 = await call(s, "memory_lookup_project", project_name="Smoke Proj")
            check("lookup reports exists after writes", look2["exists"] is True, look2)

            # Session summary and reload.
            await call(s, "memory_submit_summary", session_id="sess-1",
                       body="smoke: wiring tools", project_name="Smoke Proj")
            dig1 = await call(s, "memory_get_continuity_digest")
            check("digest shows chat", any(c["session_id"] == "sess-1"
                  for c in dig1["recent_chats"]), dig1["recent_chats"])
            sess = await call(s, "memory_get_session", session_id="sess-1")
            check("session reload has trail", any(t["title"] == "wire smoke"
                  for t in sess["trail"]), sess)

            # Project-level summary.
            await call(s, "memory_submit_summary", body="project-level recap",
                       project_name="Smoke Proj")
            summ = await call(s, "memory_get_summary", project_name="Smoke Proj")
            check("project summary excludes chat summary",
                  summ["summary"] == "project-level recap", summ)

            # Search and list.
            srch = await call(s, "memory_search", query="smoke", project_name="Smoke Proj")
            check("search ranked", srch["matches"] and 0 <= srch["matches"][0]["score"] <= 1,
                  srch["matches"][:1])
            thr = await call(s, "memory_list_threads", project_name="Smoke Proj", limit=50)
            decs = await call(s, "memory_list_decisions", project_name="Smoke Proj")
            check("list decisions", any(d["title"] == "use sqlite" for d in decs["decisions"]), decs)


            bun = await call(s, "memory_get_context_bundle", project_name="Smoke Proj",
                             budget_tokens=800)
            check("bundle within budget", bun["used_tokens"] <= 800, bun["used_tokens"])

            # Record lifecycle.
            await call(s, "memory_update_status", record_id=ing["record_id"], status="done")
            bun2 = await call(s, "memory_get_context_bundle", project_name="Smoke Proj")
            check("done task leaves bundle",
                  all(sl["title"] != "wire smoke" for sl in bun2["slices"]),
                  [sl["title"] for sl in bun2["slices"]])
            corr = await call(s, "memory_correct", record_id=dec and ing["record_id"],
                              title="wire smoke", body="updated body")
            check("correct supersedes", corr["superseded_id"] == ing["record_id"], corr)

            # Supersede records.
            a = await call(s, "memory_ingest", type="thread", title="old", body="v1",
                           project_name="Smoke Proj")
            b = await call(s, "memory_ingest", type="thread", title="new", body="v2",
                           project_name="Smoke Proj")
            sup = await call(s, "memory_supersede", old_record_id=a["record_id"],
                             new_record_id=b["record_id"])
            check("supersede", sup["superseded_by_id"] == b["record_id"], sup)

            # Project aliases and merges.
            await call(s, "memory_ingest", type="task", title="dup work", body="x",
                       project_name="Smoke Dup")
            await call(s, "memory_alias_project", alias="alias-smoke", project_id="name-smoke-dup")
            aliased = await call(s, "memory_get_project_state", project_id="alias-smoke")
            check("alias resolves", aliased["project_id"] == "name-smoke-dup", aliased)
            dups = await call(s, "memory_find_duplicates")
            check("find_duplicates read-only", "pairs" in dups, dups)
            merged = await call(s, "memory_merge_projects",
                                from_project_id="name-smoke-dup", to_project_id="name-smoke-proj")
            check("merge moves data", merged["to_id"] == "name-smoke-proj", merged)

            # Deletion and purging.
            await expect_error(s, "delete refused without confirm",
                               "memory_delete", record_ids=[b["record_id"]])
            deleted = await call(s, "memory_delete", record_ids=[b["record_id"]], confirm=True)
            check("hard delete", deleted["deleted"] == 1, deleted)
            gone = await call(s, "memory_search", query="new", project_name="Smoke Proj")
            check("deleted record not searchable",
                  all(m["record_id"] != b["record_id"] for m in gone["matches"]), gone["matches"])
            await expect_error(s, "purge refused without confirm",
                               "memory_purge_project", project_id="name-smoke-proj")
            purged = await call(s, "memory_purge_project",
                                project_id="name-smoke-proj", confirm=True)
            check("purge deletes project", purged["project_id"] == "name-smoke-proj", purged)
            after = await call(s, "memory_lookup_project", project_name="Smoke Proj")
            check("purged project is gone", after["exists"] is False, after)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="amx_smoke_")
    db = os.path.join(tmp, "smoke.db")
    asyncio.run(run(db))
    npass = sum(1 for _, ok, _ in results if ok)
    nfail = len(results) - npass
    print("\n===== AMX SMOKE TEST =====")
    for name, ok, detail in results:
        line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
        print(line + (f"  -- {detail}" if not ok else ""))
    print(f"\n{npass} passed / {nfail} failed / {len(results)} checks")
    for f in (db, db + "-wal", db + "-shm"):
        try:
            os.remove(f)
        except OSError:
            pass
    try:
        os.rmdir(tmp)
    except OSError:
        pass
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())
