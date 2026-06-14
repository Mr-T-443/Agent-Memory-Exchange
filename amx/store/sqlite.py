"""SQLite database storage backend."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from amx.schema import (
    INACTIVE_STATUSES,
    Decision,
    MemoryRecord,
    RecordType,
    Summary,
    utcnow,
)
from amx.store.migrations import apply_migrations
from amx.utils.text import content_hash

_TOKEN_RE = re.compile(r"\w+")


# Raised when the database file is corrupted or invalid.
class CorruptDatabaseError(RuntimeError):
    def __init__(self, db_path):
        self.db_path = db_path
        super().__init__(
            f"The AMX database at {db_path} is corrupted or not a database. "
            f"Restore a backup with 'amx restore <file.db>', or reset it with "
            f"'amx nukeit' (this erases memory)."
        )

# Filter for active records.
_INACTIVE_LIST = ", ".join(f"'{s}'" for s in sorted(INACTIVE_STATUSES))
_ACTIVE_CLAUSE = f"AND (status IS NULL OR status NOT IN ({_INACTIVE_LIST}))"


# Quote tokens to escape FTS5 query syntax.
def _fts_query(text: str) -> str:
    tokens = _TOKEN_RE.findall(text)
    return " OR ".join(f'"{t}"' for t in tokens)


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        project_id=row["project_id"],
        type=RecordType(row["type"]),
        title=row["title"],
        body=row["body"],
        entities=json.loads(row["entities_json"]),
        token_estimate=row["token_estimate"],
        content_hash=row["content_hash"],
        status=row["status"],
        superseded_by_id=row["superseded_by_id"],
        session_id=row["session_id"],
        schema_version=row["schema_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Allow cross-thread connection sharing for FastMCP worker threads.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            apply_migrations(self._conn)
        except sqlite3.DatabaseError as e:
            self._conn.close()
            raise CorruptDatabaseError(db_path) from e
        self._backfill_content_hashes()

    def close(self) -> None:
        self._conn.close()

    # -- meta --

    def get_meta(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value)
        )
        self._conn.commit()

    # -- tool-call log --

    # Log tool usage metrics.
    def record_tool_call(self, tool: str, project_id: Optional[str] = None) -> None:
        self._conn.execute(
            "INSERT INTO tool_log(tool, project_id, created_at) VALUES (?, ?, ?)",
            (tool, project_id, utcnow().isoformat()),
        )
        self._conn.commit()

    def tool_call_counts(self) -> dict:
        rows = self._conn.execute(
            "SELECT tool, COUNT(*) AS n FROM tool_log GROUP BY tool"
        ).fetchall()
        return {row["tool"]: int(row["n"]) for row in rows}

    # -- user profile --
    # Profile is stored in the meta table.

    def get_profile(self) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'profile_text'"
        ).fetchone()
        if row is None:
            return None
        updated = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'profile_updated_at'"
        ).fetchone()
        return {"text": row["value"], "updated_at": updated["value"] if updated else None}

    def set_profile(self, text: str) -> None:
        for key, value in (
            ("profile_text", text),
            ("profile_updated_at", utcnow().isoformat()),
        ):
            self._conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value)
            )
        self._conn.commit()

    def clear_profile(self) -> None:
        self._conn.execute(
            "DELETE FROM meta WHERE key IN ('profile_text', 'profile_updated_at')"
        )
        self._conn.commit()

    # -- projects --

    def ensure_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        root_path: Optional[str] = None,
        git_remote: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO projects(project_id, name, root_path, git_remote, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(project_id) DO NOTHING""",
            (project_id, name, root_path, git_remote, utcnow().isoformat()),
        )
        self._conn.commit()

    # -- records --

    def insert_record(self, record: MemoryRecord) -> int:
        digest = record.content_hash or content_hash(
            record.type.value, record.title, record.body
        )
        cur = self._conn.execute(
            """INSERT INTO records
               (project_id, type, title, body, entities_json, token_estimate,
                content_hash, status, superseded_by_id, session_id, schema_version,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.project_id,
                record.type.value,
                record.title,
                record.body,
                json.dumps(record.entities),
                record.token_estimate,
                digest,
                record.status,
                record.superseded_by_id,
                record.session_id,
                record.schema_version,
                record.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def find_record_by_hash(
        self, project_id: str, digest: str
    ) -> Optional[MemoryRecord]:
        row = self._conn.execute(
            """SELECT * FROM records WHERE project_id = ? AND content_hash = ?
               ORDER BY id LIMIT 1""",
            (project_id, digest),
        ).fetchone()
        return _row_to_record(row) if row else None

    # Backfill missing content hashes.
    def _backfill_content_hashes(self) -> None:
        rows = self._conn.execute(
            "SELECT id, type, title, body FROM records WHERE content_hash IS NULL"
        ).fetchall()
        for row in rows:
            self._conn.execute(
                "UPDATE records SET content_hash = ? WHERE id = ?",
                (content_hash(row["type"], row["title"], row["body"]), row["id"]),
            )
        if rows:
            self._conn.commit()

    # Search records using FTS5.
    def search_records(
        self, project_id: str, query: str, limit: int, active_only: bool = False
    ) -> list[tuple[MemoryRecord, float]]:
        fts = _fts_query(query)
        if not fts:
            return []
        rows = self._conn.execute(
            f"""SELECT r.*, bm25(records_fts) AS rank
               FROM records_fts
               JOIN records r ON r.id = records_fts.rowid
               WHERE records_fts MATCH ? AND r.project_id = ?
                 {_ACTIVE_CLAUSE if active_only else ""}
               ORDER BY rank LIMIT ?""",
            (fts, project_id, limit),
        ).fetchall()
        return [(_row_to_record(row), float(row["rank"])) for row in rows]

    # Return the text window around the query match (FTS5 snippet), or "" if none.
    def fts_snippet(self, record_id: int, query: str, max_tokens: int = 20) -> str:
        fts = _fts_query(query)
        if not fts:
            return ""
        row = self._conn.execute(
            f"SELECT snippet(records_fts, -1, '', '', '...', {int(max_tokens)}) AS snip "
            "FROM records_fts WHERE records_fts MATCH ? AND rowid = ?",
            (fts, record_id),
        ).fetchone()
        return row["snip"].strip() if row and row["snip"] else ""

    def recent_records(
        self,
        project_id: str,
        limit: int,
        type: Optional[RecordType] = None,
        active_only: bool = False,
    ) -> list[MemoryRecord]:
        active = _ACTIVE_CLAUSE if active_only else ""
        if type is None:
            rows = self._conn.execute(
                f"""SELECT * FROM records WHERE project_id = ? {active}
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"""SELECT * FROM records WHERE project_id = ? AND type = ? {active}
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (project_id, type.value, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def record_count(self, project_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM records WHERE project_id = ?", (project_id,)
        ).fetchone()
        return int(row[0])

    # Export records matching the Foundry schema.
    def export_records(self, project_id: Optional[str] = None) -> list[sqlite3.Row]:
        if project_id is None:
            return self._conn.execute(
                "SELECT id, project_id, type, title, body FROM records"
            ).fetchall()
        return self._conn.execute(
            "SELECT id, project_id, type, title, body FROM records WHERE project_id = ?",
            (project_id,),
        ).fetchall()

    # -- record lifecycle --

    def get_record(self, record_id: int) -> Optional[MemoryRecord]:
        row = self._conn.execute(
            "SELECT * FROM records WHERE id = ?", (record_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    # Update record status.
    def set_record_status(
        self, record_id: int, status: Optional[str], superseded_by_id: Optional[int] = None
    ) -> None:
        self._conn.execute(
            "UPDATE records SET status = ?, superseded_by_id = ? WHERE id = ?",
            (status, superseded_by_id, record_id),
        )
        self._conn.commit()

    # Permanently delete records by ID.
    def delete_records(self, record_ids: list[int]) -> int:
        if not record_ids:
            return 0
        marks = ", ".join("?" for _ in record_ids)
        cur = self._conn.execute(
            f"DELETE FROM records WHERE id IN ({marks})", tuple(record_ids)
        )
        self._conn.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
        self._conn.commit()
        return cur.rowcount

    # Permanently delete all data associated with a project.
    def purge_project(self, project_id: str) -> dict:
        counts: dict[str, int] = {}
        for table in ("records", "decisions", "summaries", "project_state", "tool_log"):
            cur = self._conn.execute(
                f"DELETE FROM {table} WHERE project_id = ?", (project_id,)
            )
            counts[table] = cur.rowcount

        self._conn.execute(
            "DELETE FROM project_aliases WHERE project_id = ? OR alias_id = ?",
            (project_id, project_id),
        )
        self._conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        self._conn.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
        self._conn.commit()
        return counts

    # Search records across all projects.
    def search_records_global(
        self, query: str, limit: int
    ) -> list[tuple[MemoryRecord, float]]:
        fts = _fts_query(query)
        if not fts:
            return []
        rows = self._conn.execute(
            """SELECT r.*, bm25(records_fts) AS rank
               FROM records_fts
               JOIN records r ON r.id = records_fts.rowid
               WHERE records_fts MATCH ?
               ORDER BY rank LIMIT ?""",
            (fts, limit),
        ).fetchall()
        return [(_row_to_record(row), float(row["rank"])) for row in rows]

    # -- discovery --

    def get_project(self, project_id: str) -> Optional[dict]:
        row = self._conn.execute(
            """SELECT project_id, name, root_path, git_remote, created_at
               FROM projects WHERE project_id = ?""",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None

    # Get timestamp of last project activity.
    def project_last_activity(self, project_id: str) -> Optional[str]:
        row = self._conn.execute(
            """SELECT MAX(ts) AS last FROM (
                   SELECT created_at AS ts FROM records WHERE project_id = ?
                   UNION ALL SELECT created_at FROM decisions WHERE project_id = ?
                   UNION ALL SELECT created_at FROM summaries WHERE project_id = ?
                   UNION ALL SELECT updated_at FROM project_state WHERE project_id = ?
               )""",
            (project_id, project_id, project_id, project_id),
        ).fetchone()
        return row["last"] if row and row["last"] else None

    # List projects sorted by recent activity.
    def list_projects_by_activity(self, limit: int) -> list[dict]:
        rows = self._conn.execute(
            """SELECT p.project_id, p.name, p.root_path, p.git_remote,
                      MAX(COALESCE(act.ts, p.created_at)) AS last_activity
               FROM projects p
               LEFT JOIN (
                   SELECT project_id, created_at AS ts FROM records
                   UNION ALL SELECT project_id, created_at FROM decisions
                   UNION ALL SELECT project_id, created_at FROM summaries
                   UNION ALL SELECT project_id, updated_at FROM project_state
               ) act ON act.project_id = p.project_id
               GROUP BY p.project_id, p.name, p.root_path, p.git_remote
               ORDER BY last_activity DESC, p.project_id
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def all_projects(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT project_id, name, root_path, git_remote, created_at FROM projects"
        ).fetchall()
        return [dict(row) for row in rows]

    def project_content_hashes(self, project_id: str) -> set[str]:
        rows = self._conn.execute(
            """SELECT DISTINCT content_hash FROM records
               WHERE project_id = ? AND content_hash IS NOT NULL""",
            (project_id,),
        ).fetchall()
        return {row["content_hash"] for row in rows}

    def project_entities(self, project_id: str, limit: int = 50) -> set[str]:
        rows = self._conn.execute(
            """SELECT entities_json FROM records WHERE project_id = ?
               ORDER BY id DESC LIMIT ?""",
            (project_id, limit),
        ).fetchall()
        out: set[str] = set()
        for row in rows:
            out.update(json.loads(row["entities_json"]))
        return out

    # -- identity reconciliation --

    # Resolve canonical project ID from an alias.
    def canonical_project_id(self, project_id: str) -> str:
        row = self._conn.execute(
            "SELECT project_id FROM project_aliases WHERE alias_id = ?", (project_id,)
        ).fetchone()
        return row["project_id"] if row else project_id

    # Add project alias mapping.
    def add_alias(self, alias_id: str, project_id: str, source: str = "manual") -> None:
        target = self.canonical_project_id(project_id)
        self._conn.execute(
            """INSERT INTO project_aliases(alias_id, project_id, source, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(alias_id) DO UPDATE SET project_id = excluded.project_id""",
            (alias_id, target, source, utcnow().isoformat()),
        )
        self._conn.commit()

    def remove_alias(self, alias_id: str) -> None:
        self._conn.execute(
            "DELETE FROM project_aliases WHERE alias_id = ?", (alias_id,)
        )
        self._conn.commit()

    # Merge all data from one project into another.
    def merge_projects(self, from_id: str, to_id: str) -> dict:
        from_id = self.canonical_project_id(from_id)
        to_id = self.canonical_project_id(to_id)
        if from_id == to_id:
            raise ValueError("Cannot merge a project into itself.")

        # Snapshot IDs before merge.
        moved: dict[str, list[int]] = {}
        for table in ("records", "decisions", "summaries"):
            rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE project_id = ?", (from_id,)
            ).fetchall()
            moved[table] = [row["id"] for row in rows]

        src_state = self.get_state(from_id)

        for table in ("records", "decisions", "summaries", "tool_log"):
            self._conn.execute(
                f"UPDATE {table} SET project_id = ? WHERE project_id = ?",
                (to_id, from_id),
            )

        self._move_project_state(from_id, to_id, src_state)

        cur = self._conn.execute(
            """INSERT INTO project_merges(from_id, to_id, moved_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (
                from_id,
                to_id,
                json.dumps({**moved, "source_state": src_state}),
                utcnow().isoformat(),
            ),
        )
        merge_id = int(cur.lastrowid)

        self._conn.execute(
            """INSERT INTO project_aliases(alias_id, project_id, source, created_at)
               VALUES (?, ?, 'merge', ?)
               ON CONFLICT(alias_id) DO UPDATE SET project_id = excluded.project_id""",
            (from_id, to_id, utcnow().isoformat()),
        )
        # Re-point existing aliases so none still point at the now-aliased from_id.
        self._conn.execute(
            "UPDATE project_aliases SET project_id = ? WHERE project_id = ?",
            (to_id, from_id),
        )
        self._conn.commit()

        return {
            "merge_id": merge_id,
            "from_id": from_id,
            "to_id": to_id,
            "moved": {table: len(ids) for table, ids in moved.items()},
        }

    # Resolve project state conflict on merge.
    def _move_project_state(self, from_id: str, to_id: str, src_state: Optional[dict]) -> None:
        if src_state is None:
            return
        if self.get_state(to_id) is None:
            self._conn.execute(
                "UPDATE project_state SET project_id = ? WHERE project_id = ?",
                (to_id, from_id),
            )
        else:
            self._conn.execute(
                "DELETE FROM project_state WHERE project_id = ?", (from_id,)
            )

    # -- project state --

    def get_state(self, project_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT state_json FROM project_state WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def set_state(self, project_id: str, state: dict) -> None:
        self._conn.execute(
            """INSERT INTO project_state(project_id, state_json, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 state_json = excluded.state_json,
                 updated_at = excluded.updated_at""",
            (project_id, json.dumps(state, sort_keys=True), utcnow().isoformat()),
        )
        self._conn.commit()

    # -- decisions --

    def insert_decision(self, decision: Decision) -> int:
        cur = self._conn.execute(
            """INSERT INTO decisions(project_id, title, rationale, supersedes_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                decision.project_id,
                decision.title,
                decision.rationale,
                decision.supersedes_id,
                decision.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # List decisions, optionally excluding superseded ones.
    def list_decisions(
        self, project_id: str, limit: int, exclude_superseded: bool = False
    ) -> list[Decision]:
        clause = ""
        if exclude_superseded:
            clause = (
                "AND id NOT IN (SELECT supersedes_id FROM decisions "
                "WHERE project_id = ? AND supersedes_id IS NOT NULL)"
            )
            params = (project_id, project_id, limit)
        else:
            params = (project_id, limit)

        rows = self._conn.execute(
            f"""SELECT * FROM decisions WHERE project_id = ? {clause}
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            params,
        ).fetchall()
        return [
            Decision(
                id=row["id"],
                project_id=row["project_id"],
                title=row["title"],
                rationale=row["rationale"],
                supersedes_id=row["supersedes_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    # -- summaries --

    def insert_summary(self, summary: Summary) -> int:
        cur = self._conn.execute(
            """INSERT INTO summaries(project_id, kind, body, source, session_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                summary.project_id,
                summary.kind,
                summary.body,
                summary.source,
                summary.session_id,
                summary.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    # Retrieve latest project-level summary.
    def latest_summary(self, project_id: str) -> Optional[Summary]:
        row = self._conn.execute(
            """SELECT * FROM summaries WHERE project_id = ? AND session_id IS NULL
               ORDER BY created_at DESC, id DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        return self._row_to_summary(row) if row else None

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> Summary:
        return Summary(
            id=row["id"],
            project_id=row["project_id"],
            kind=row["kind"],
            body=row["body"],
            source=row["source"],
            session_id=row["session_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # -- session continuity --

    # Update or insert session summary.
    def upsert_session_summary(
        self, session_id: str, project_id: str, body: str
    ) -> int:
        row = self._conn.execute(
            "SELECT id FROM summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        now = utcnow().isoformat()

        if row is not None:
            self._conn.execute(
                """UPDATE summaries SET project_id = ?, body = ?, created_at = ?
                   WHERE id = ?""",
                (project_id, body, now, row["id"]),
            )
            self._conn.commit()
            return int(row["id"])

        cur = self._conn.execute(
            """INSERT INTO summaries(project_id, kind, body, source, session_id, created_at)
               VALUES (?, 'session', ?, 'client', ?, ?)""",
            (project_id, body, session_id, now),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def session_summary(self, session_id: str) -> Optional[Summary]:
        row = self._conn.execute(
            "SELECT * FROM summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        return self._row_to_summary(row) if row else None

    # Retrieve recent session summaries.
    def recent_session_summaries(self, limit: int) -> list[Summary]:
        rows = self._conn.execute(
            """SELECT * FROM summaries WHERE session_id IS NOT NULL
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def session_records(
        self, session_id: str, limit: int, active_only: bool = False
    ) -> list[MemoryRecord]:
        active = _ACTIVE_CLAUSE if active_only else ""
        rows = self._conn.execute(
            f"""SELECT * FROM records WHERE session_id = ? {active}
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [_row_to_record(row) for row in rows]
