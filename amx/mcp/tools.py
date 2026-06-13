"""MCP tool handlers mapping to amx.* MCP tools."""

from __future__ import annotations

import functools
from typing import Optional

from amx import adoption
from amx.config import AMXConfig
from amx.integrations import foundry_iq
from amx.identity import resolve_project_id
from amx.identity.reconcile import find_duplicate_projects
from amx.memory.bundle import build_bundle
from amx.memory.digest import build_continuity_digest
from amx.memory.discovery import discover_projects
from amx.memory.ingest import ingest_record
from amx.memory.retrieval import search_memory
from amx.memory.summary import get_or_build_summary, submit_summary
from amx.schema import LIFECYCLE_TYPES, SETTABLE_STATUSES, RecordType
from amx.state.decision_log import list_decisions, record_decision
from amx.state.project_state import get_project_state, update_project_state
from amx.store import Store
from amx.utils.token_budget import estimate_tokens


    # Register MCP tools on FastMCP.
def register_tools(mcp, store: Store, cfg: AMXConfig) -> None:

    # Log tool execution metrics without arguments.
    def logged(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                store.record_tool_call(
                    fn.__name__, kwargs.get("project_id") or kwargs.get("project_name")
                )
            except Exception:
                pass
            return fn(*args, **kwargs)
        return wrapper

    def tool(fn):
        return mcp.tool()(logged(fn))

    def _resolve(
        project_id: Optional[str], project_name: Optional[str], cwd: Optional[str]
    ) -> str:
        pid, root, remote = resolve_project_id(project_id, project_name, cwd)
        pid = store.canonical_project_id(pid)
        store.ensure_project(pid, name=project_name, root_path=root, git_remote=remote)
        return pid

    @tool
    def amx_init(client: Optional[str] = None, applied: bool = False) -> dict:
        """Onboard AMX into the AI client by generating its continuity instruction."""
        return adoption.init(store, client=client, applied=applied)

    @tool
    def memory_get_profile() -> dict:
        """Retrieve the user profile detailing developer background and stack."""
        profile = store.get_profile()
        if profile is None:
            return {"profile": None, "note": "No profile set yet."}
        return {"profile": profile["text"], "updated_at": profile["updated_at"]}

    @tool
    def memory_set_profile(text: str) -> dict:
        """Create or update the user profile."""
        if not text.strip():
            store.clear_profile()
            return {"ok": True, "cleared": True}
        est = estimate_tokens(text)
        if est > cfg.profile_max_tokens:
            raise ValueError(
                f"Profile is ~{est} tokens; the cap is {cfg.profile_max_tokens}. "
                "Shorten the text and retry (or raise AMX_PROFILE_MAX_TOKENS)."
            )
        store.set_profile(text)
        return {"ok": True, "token_estimate": est}

    @tool
    def memory_discover_projects(
        description: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """Search and identify projects by description or recent activity."""
        return discover_projects(store, description, cfg, limit).model_dump()

    @tool
    def memory_get_continuity_digest(budget_tokens: Optional[int] = None) -> dict:
        """Retrieve a summary digest of recent active sessions and the user profile."""
        return build_continuity_digest(store, cfg, budget_tokens)

    @tool
    def memory_lookup_project(
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Look up an existing project by ID, name, or working directory."""
        pid, _root, _remote = resolve_project_id(project_id, project_name, cwd)
        canonical = store.canonical_project_id(pid)
        project = store.get_project(canonical)
        dups = find_duplicate_projects(store, cfg, canonical) if project else {"pairs": []}
        return {
            "resolved_project_id": canonical,
            "exists": project is not None,
            "record_count": store.record_count(canonical),
            "name": project.get("name") if project else project_name,
            "last_activity": store.project_last_activity(canonical),
            "possible_duplicates": dups.get("pairs", []),
        }

    @tool
    def memory_get_session(session_id: str, limit: int = 20) -> dict:
        """Retrieve a specific chat session's memory trail and summary."""
        records = store.session_records(session_id, limit)
        summary = store.session_summary(session_id)
        trail = []
        for record in records:
            trail.append(
                {
                    "record_id": record.id,
                    "type": record.type.value,
                    "title": record.title,
                    "body": record.body,
                    "created_at": record.created_at.isoformat(),
                }
            )
        return {
            "session_id": session_id,
            "summary": summary.body if summary else None,
            "project_id": summary.project_id if summary else None,
            "trail": trail,
        }

    @tool
    def memory_ingest(
        type: str,
        title: str,
        body: str,
        entities: Optional[list[str]] = None,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """Ingest a new record (task, bug, research, or entity) into project memory."""
        pid = _resolve(project_id, project_name, cwd)
        record = ingest_record(
            store, pid, RecordType(type), title, body, entities, session_id=session_id
        )
        if cfg.foundry_sync_enabled and not record.deduped:
            foundry_iq.push_record(record.id, pid, type, title, body, cfg)
        return {
            "record_id": record.id,
            "project_id": pid,
            "token_estimate": record.token_estimate,
            "deduped": record.deduped,
        }

    @tool
    def memory_search(
        query: str,
        limit: int = 10,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Search project memory and grounded resources for relevant matches."""
        pid = _resolve(project_id, project_name, cwd)
        result = search_memory(store, pid, query, limit, cfg)
        return result.model_dump()

    @tool
    def memory_get_project_state(
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Retrieve the project's current canonical state."""
        pid = _resolve(project_id, project_name, cwd)
        return {"project_id": pid, "state": get_project_state(store, pid)}

    @tool
    def memory_update_project_state(
        patch: dict,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Update project state with a key-value patch."""
        pid = _resolve(project_id, project_name, cwd)
        state = update_project_state(store, pid, patch)
        return {"project_id": pid, "state": state}

    @tool
    def memory_get_summary(
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Retrieve the project's latest summary."""
        pid = _resolve(project_id, project_name, cwd)
        summary = get_or_build_summary(store, pid)
        if summary is None:
            return {"project_id": pid, "summary": None}
        return {"project_id": pid, "summary": summary.body, "source": summary.source}

    @tool
    def memory_submit_summary(
        body: str,
        kind: str = "session",
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """Submit a project checkpoint or a single-line session summary."""
        pid = _resolve(project_id, project_name, cwd)

        if session_id:
            est = estimate_tokens(body)
            if est > cfg.chat_summary_max_tokens:
                raise ValueError(
                    f"Chat summary is ~{est} tokens; the cap is "
                    f"{cfg.chat_summary_max_tokens}. Shorten it (it should be one "
                    "tiny line), or raise AMX_CHAT_SUMMARY_MAX_TOKENS."
                )
            sid = store.upsert_session_summary(session_id, pid, body)
            return {"project_id": pid, "summary_id": sid, "session_id": session_id}

        summary = submit_summary(store, pid, body, kind)
        return {"project_id": pid, "summary_id": summary.id}

    @tool
    def memory_get_context_bundle(
        query: Optional[str] = None,
        budget_tokens: Optional[int] = None,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Retrieve a token-budgeted bundle of project state, summary, and decisions."""
        pid = _resolve(project_id, project_name, cwd)
        bundle = build_bundle(store, pid, cfg, query=query, budget_tokens=budget_tokens)
        return bundle.model_dump()

    @tool
    def memory_record_decision(
        title: str,
        rationale: str,
        supersedes_id: Optional[int] = None,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Record a project decision with its rationale."""
        pid = _resolve(project_id, project_name, cwd)
        decision = record_decision(store, pid, title, rationale, supersedes_id)
        return {"project_id": pid, "decision_id": decision.id}

    @tool
    def memory_checkpoint(
        summary: Optional[str] = None,
        decisions: Optional[list[dict]] = None,
        records: Optional[list[dict]] = None,
        state: Optional[dict] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Record a batch of decisions, records, state changes, and summaries in one call."""
        pid = _resolve(project_id, project_name, cwd)
        out: dict = {"project_id": pid}

        if decisions:
            out["decisions"] = [
                record_decision(
                    store, pid, d["title"], d["rationale"], d.get("supersedes_id")
                ).id
                for d in decisions
            ]

        if records:
            saved = []
            for r in records:
                rec = ingest_record(
                    store, pid, RecordType(r["type"]), r["title"], r["body"],
                    r.get("entities"), session_id=session_id,
                )
                if cfg.foundry_sync_enabled and not rec.deduped:
                    foundry_iq.push_record(
                        rec.id, pid, r["type"], r["title"], r["body"], cfg
                    )
                saved.append({"record_id": rec.id, "deduped": rec.deduped})
            out["records"] = saved

        if state:
            out["state"] = update_project_state(store, pid, state)

        if summary:
            if session_id:
                est = estimate_tokens(summary)
                if est > cfg.chat_summary_max_tokens:
                    raise ValueError(
                        f"Chat summary is ~{est} tokens; the cap is "
                        f"{cfg.chat_summary_max_tokens}. Keep it to one tiny line "
                        "(or raise AMX_CHAT_SUMMARY_MAX_TOKENS)."
                    )
                sid = store.upsert_session_summary(session_id, pid, summary)
                out["summary"] = {"session_id": session_id, "summary_id": sid}
            else:
                saved_summary = submit_summary(store, pid, summary, "session")
                out["summary"] = {"summary_id": saved_summary.id}

        return out

    @tool
    def memory_list_threads(
        limit: int = 10,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Retrieve recent conversation threads for the project."""
        pid = _resolve(project_id, project_name, cwd)
        threads = store.recent_records(pid, limit, type=RecordType.THREAD)
        thread_briefs = []
        for thread in threads:
            thread_briefs.append(
                {
                    "record_id": thread.id,
                    "title": thread.title,
                    "created_at": thread.created_at.isoformat(),
                }
            )
        return {"project_id": pid, "threads": thread_briefs}

    @tool
    def memory_list_decisions(
        limit: int = 10,
        project_id: Optional[str] = None,
        project_name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> dict:
        """Retrieve the project decision log."""
        pid = _resolve(project_id, project_name, cwd)
        decision_briefs = []
        for decision in list_decisions(store, pid, limit):
            decision_briefs.append(
                {
                    "decision_id": decision.id,
                    "title": decision.title,
                    "rationale": decision.rationale,
                    "supersedes_id": decision.supersedes_id,
                }
            )
        return {"project_id": pid, "decisions": decision_briefs}

    @tool
    def memory_update_status(record_id: int, status: str) -> dict:
        """Update status of a task or bug record."""
        record = store.get_record(record_id)
        if record is None:
            raise ValueError(f"No record with id {record_id}.")
        if record.type not in LIFECYCLE_TYPES:
            raise ValueError(
                f"Status applies only to task/bug records; {record_id} is a "
                f"{record.type.value}. Use memory_supersede/memory_correct instead."
            )
        if status not in SETTABLE_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}; choose from {sorted(SETTABLE_STATUSES)}."
            )
        store.set_record_status(record_id, status)
        return {"record_id": record_id, "status": status, "project_id": record.project_id}

    @tool
    def memory_supersede(old_record_id: int, new_record_id: int) -> dict:
        """Mark a record as superseded by a newer record."""
        old = store.get_record(old_record_id)
        new = store.get_record(new_record_id)
        if old is None or new is None:
            missing = old_record_id if old is None else new_record_id
            raise ValueError(f"No record with id {missing}.")
        store.set_record_status(old_record_id, "superseded", superseded_by_id=new_record_id)
        return {
            "superseded_id": old_record_id,
            "superseded_by_id": new_record_id,
            "project_id": old.project_id,
        }

    @tool
    def memory_find_duplicates(project_id: Optional[str] = None) -> dict:
        """Find potential duplicate project records in the database."""
        return find_duplicate_projects(store, cfg, project_id)

    @tool
    def memory_merge_projects(from_project_id: str, to_project_id: str) -> dict:
        """Merge memory from one project into another."""
        if from_project_id == to_project_id:
            raise ValueError("from and to are the same project.")
        from_canon = store.canonical_project_id(from_project_id)
        to_canon = store.canonical_project_id(to_project_id)
        if from_canon == to_canon:
            raise ValueError("Those ids already resolve to the same project.")
        if store.get_project(from_canon) is None:
            raise ValueError(f"No project {from_project_id!r}.")
        if store.get_project(to_canon) is None:
            raise ValueError(f"No project {to_project_id!r}.")
        result = store.merge_projects(from_canon, to_canon)
        # Refresh Foundry documents with new canonical project ID.
        if cfg.foundry_sync_enabled:
            try:
                foundry_iq.push_records(store.export_records(to_canon), cfg)
            except Exception:
                pass
        return result

    @tool
    def memory_alias_project(alias: str, project_id: str) -> dict:
        """Point a project alias ID to a canonical project ID."""
        canonical = store.canonical_project_id(project_id)
        if alias == canonical:
            raise ValueError("An id cannot alias itself.")
        if store.get_project(canonical) is None:
            raise ValueError(f"No canonical project {project_id!r}.")
        store.add_alias(alias, canonical, source="manual")
        return {"alias": alias, "project_id": canonical}

    @tool
    def memory_correct(record_id: int, title: str, body: str) -> dict:
        """Correct an existing memory record by replacing it."""
        old = store.get_record(record_id)
        if old is None:
            raise ValueError(f"No record with id {record_id}.")
        if old.status == "superseded":
            raise ValueError(
                f"Record {record_id} is already superseded by "
                f"{old.superseded_by_id}; correct the current record instead."
            )
        new = ingest_record(store, old.project_id, old.type, title, body)
        if new.id != record_id:
            store.set_record_status(record_id, "superseded", superseded_by_id=new.id)
        if cfg.foundry_sync_enabled and not new.deduped:
            foundry_iq.push_record(new.id, old.project_id, old.type.value, title, body, cfg)
        return {
            "record_id": new.id,
            "superseded_id": record_id,
            "project_id": old.project_id,
            "deduped": new.deduped,
        }

    @tool
    def memory_delete(record_ids: list[int], confirm: bool = False) -> dict:
        """Permanently delete specific records by ID."""
        if not confirm:
            raise ValueError(
                "Refusing to delete without confirm=True. Confirm with the user "
                "that these records should be permanently erased, then retry."
            )
        deleted = store.delete_records(record_ids)
        if cfg.foundry_sync_enabled:
            foundry_iq.delete_records(record_ids, cfg)
        return {"requested": len(record_ids), "deleted": deleted}

    @tool
    def memory_purge_project(project_id: str, confirm: bool = False) -> dict:
        """Permanently delete all data for a project."""
        if not confirm:
            raise ValueError(
                "Refusing to purge without confirm=True. Show the user what will "
                "be erased and get explicit confirmation, then retry."
            )
        canonical = store.canonical_project_id(project_id)
        if store.get_project(canonical) is None:
            raise ValueError(f"No project {project_id!r}.")
        if cfg.foundry_sync_enabled:
            # Collect IDs before they are purged.
            ids = [r["id"] for r in store.export_records(canonical)]
        counts = store.purge_project(canonical)
        if cfg.foundry_sync_enabled:
            foundry_iq.delete_records(ids, cfg)
        return {"project_id": canonical, "deleted": counts}
