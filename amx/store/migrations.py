"""Database schema migrations."""

from __future__ import annotations

MIGRATIONS: list[str] = [
    # v1: Initial schema.
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE projects (
        project_id TEXT PRIMARY KEY,
        name       TEXT,
        root_path  TEXT,
        git_remote TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE records (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id     TEXT NOT NULL,
        type           TEXT NOT NULL,
        title          TEXT NOT NULL,
        body           TEXT NOT NULL,
        entities_json  TEXT NOT NULL DEFAULT '[]',
        token_estimate INTEGER NOT NULL DEFAULT 0,
        schema_version INTEGER NOT NULL,
        created_at     TEXT NOT NULL
    );
    CREATE INDEX idx_records_project ON records(project_id, created_at DESC);
    CREATE INDEX idx_records_type ON records(project_id, type, created_at DESC);

    CREATE VIRTUAL TABLE records_fts USING fts5(
        title, body, content='records', content_rowid='id'
    );
    CREATE TRIGGER records_ai AFTER INSERT ON records BEGIN
        INSERT INTO records_fts(rowid, title, body)
        VALUES (new.id, new.title, new.body);
    END;

    CREATE TABLE project_state (
        project_id TEXT PRIMARY KEY,
        state_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE decisions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id    TEXT NOT NULL,
        title         TEXT NOT NULL,
        rationale     TEXT NOT NULL,
        supersedes_id INTEGER,
        created_at    TEXT NOT NULL
    );
    CREATE INDEX idx_decisions_project ON decisions(project_id, created_at DESC);

    CREATE TABLE summaries (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        kind       TEXT NOT NULL,
        body       TEXT NOT NULL,
        source     TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_summaries_project ON summaries(project_id, created_at DESC);
    """,

    # v2: Content-hash deduplication.
    """
    ALTER TABLE records ADD COLUMN content_hash TEXT;
    CREATE INDEX idx_records_hash ON records(project_id, content_hash);
    """,

    # v3: Tool-call log.
    """
    CREATE TABLE tool_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        tool       TEXT NOT NULL,
        project_id TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_tool_log_created ON tool_log(created_at);
    """,

    # v4: Record lifecycle (status and superseding).
    """
    ALTER TABLE records ADD COLUMN status TEXT;
    ALTER TABLE records ADD COLUMN superseded_by_id INTEGER;
    CREATE INDEX idx_records_status ON records(project_id, status);
    """,

    # v5: Identity reconciliation (aliases and merges).
    """
    CREATE TABLE project_aliases (
        alias_id   TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source     TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_aliases_target ON project_aliases(project_id);

    CREATE TABLE project_merges (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        from_id    TEXT NOT NULL,
        to_id      TEXT NOT NULL,
        moved_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,

    # v6: Session continuity support.
    """
    ALTER TABLE records ADD COLUMN session_id TEXT;
    ALTER TABLE summaries ADD COLUMN session_id TEXT;
    CREATE INDEX idx_records_session ON records(session_id, created_at DESC);
    CREATE INDEX idx_summaries_session ON summaries(session_id, created_at DESC);
    """,
]


def apply_migrations(conn) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()

    current = 0
    if row is not None:
        got = conn.execute("SELECT value FROM meta WHERE key='db_version'").fetchone()
        if got is not None:
            current = int(got[0])

    for version, script in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        conn.executescript(script)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('db_version', ?)",
            (str(version),),
        )
        conn.commit()
