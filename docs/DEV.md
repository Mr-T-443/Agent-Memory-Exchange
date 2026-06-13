# AMX — Developer Guide

For people working *on* AMX, not just with it. If you only want to use AMX, see
[WORKING.md](WORKING.md).

AMX is a passive, local-first memory layer exposed over MCP. The guiding rule:
**be ruthless about deleting complexity** — the code should feel boring, obvious,
and easy to change a year from now. Keep that in mind before adding an abstraction.

---

## Architecture

```text
Client (IDE / CLI / Web UI)
        │  MCP over stdio (JSON-RPC)
        ▼
  AMX MCP Server  (amx/mcp/server.py — FastMCP)
        │
        ├── Tool handlers   amx/mcp/tools.py    thin: validate → resolve → delegate
        ├── Identity        amx/identity/       project_id resolution + reconciliation
        ├── Memory ops      amx/memory/         ingest, retrieval, ranking, bundle,
        │                                       summary, digest, discovery
        ├── State           amx/state/          project_state, decision_log
        ├── Integrations    amx/integrations/   optional Foundry IQ grounding
        ├── Utils           amx/utils/          token estimate, text/hash
        └── Store           amx/store/          SQLite (WAL + FTS5), versioned migrations
                                   │
                                   ▼
                          ~/.amx/amx.db  (single file)
```

### Core design decisions

- **Passive server.** AMX never acts on its own. The host AI is the actuator — it
  decides when to call AMX, exactly as it decides when to run git or edit a file.
  All intelligence lives in the host model; AMX only stores, ranks, and returns. If
  you find yourself writing "AMX automatically does X," stop — the host does X, the
  instruction makes it choose to.
- **Local-first, single SQLite file** (`~/.amx/amx.db`, WAL mode). No daemon, no
  cloud. Override the path with `AMX_DB_PATH` (used heavily for test isolation).
- **Deterministic ranking, no LLM in the layer.**
  `score = 0.50·BM25 + 0.25·recency + 0.15·type_weight + 0.10·entity_overlap`, ties
  broken by record id. Same inputs → same order. AMX never calls an LLM; clients
  submit LLM-written summaries, and AMX's only fallback is extractive.
- **Token is budgeted.** The context bundle fills slices in priority order
  (profile → state → summary → decisions → ranked matches) and stops at the budget
  minus a safety margin.
- **Append-only by default, with explicit escape hatches.** Records aren't edited;
  lifecycle status hides them. Hard delete/purge were added later as deliberate,
  confirmed exceptions.
- **Thin tool handlers.** Every project-scoped tool funnels through one `_resolve()`
  helper (resolve identity → canonicalize aliases → ensure project row), so
  cross-cutting behavior lives in one place.

### The request flow

A tool call travels:

```text
client → tools.py handler → _resolve() → core module → Store → SQLite
                                                                  │
                          plain dict / Pydantic model_dump() ◄────┘
```

`_resolve(project_id, project_name, cwd)` (in `amx/mcp/tools.py`) is the chokepoint:
it calls `resolve_project_id()`, maps any alias to its canonical id with
`store.canonical_project_id()`, and ensures the project row exists. Identity and
reconciliation logic stay in one place.

---

## Components

| Module | Responsibility |
|---|---|
| `amx/schema.py` | Typed records and tool payloads (Pydantic). `SCHEMA_VERSION = 3`; lifecycle/status constants. |
| `amx/config.py` | `AMXConfig` — paths, ranking weights, budgets, all env-overridable knobs; `TYPE_WEIGHTS`. |
| `amx/identity/project_id.py` | `resolve_project_id()`: priority explicit id → `name-<slug>` → `git-<hash>` → `path-<hash>` → `default`. `_git_remote()` detaches stdin so a Git child can't block the stdio server. |
| `amx/identity/reconcile.py` | `find_duplicate_projects()`: deterministic confidence (same git remote 0.95, same name slug 0.8, path+entity 0.6, content overlap 0.5). Suggestion-only. |
| `amx/memory/ingest.py` | Idempotent ingest via a normalized `content_hash` (same fact twice = one record, `deduped=True`). |
| `amx/memory/retrieval.py` | `search_memory()` — FTS5 query → ranked matches, plus optional Foundry IQ federation. |
| `amx/memory/ranking.py` | The deterministic score blend. |
| `amx/memory/bundle.py` | `build_bundle()` — the budgeted slice builder. |
| `amx/memory/summary.py` | Client summary or extractive fallback. |
| `amx/memory/digest.py` | `build_continuity_digest()` — cold-start view (profile + recent chats). |
| `amx/memory/discovery.py` | `discover_projects()` — cross-project ranked search from a description. |
| `amx/state/project_state.py` | Shallow-merge state patches, each logged as a `raw_event`. |
| `amx/state/decision_log.py` | Dual-writes a `decisions` row and a searchable record. |
| `amx/store/sqlite.py` | The only persistence layer; **all SQL lives here**. FTS5 is an external-content table kept in sync by triggers. |
| `amx/store/migrations.py` | Six versioned, additive migrations (`db_version 6`). |
| `amx/mcp/server.py` | `create_server(cfg)` builds FastMCP with server instructions and registers tools. `main()` runs stdio. |
| `amx/adoption.py` | Per-client onboarding (`amx_init`) + the persistent continuity instruction (`INSTRUCTION_VERSION`). |
| `amx/cli.py` | The `amx` command: `server`, `version`, `info`, `install-mcp`, `backup`, `restore`, `enable-foundry`, `disable-foundry`, `foundry-sync`, `local-sync`, `update`, `uninstall`, `nukeit`. `update`/`uninstall` detect how AMX was installed — pipx, a dedicated `~/.amx-venv`, or plain pip — and clean up accordingly. |
| `amx/clients.py` | Known MCP clients — detection, config-file paths, `install()`/`uninstall()` (surgical, only AMX's own entry), and per-client auto-approve ("trust") for `amx install-mcp`. |

---

## Internal APIs

### Store (`amx/store/sqlite.py`)

The single persistence boundary. **No SQL anywhere else.** It owns records,
projects, state, decisions, summaries, aliases, merges, the tool-call log, and the
session tables. Notable behaviors:

- **FTS5** (`records_fts`) is an external-content table mirrored from `records` by
  an `AFTER INSERT` trigger. Hard deletes trigger an FTS rebuild; reassigning
  `project_id` during a merge leaves FTS intact (rowids are stable).
- **Aliases & merges:** `canonical_project_id()` resolves an alias to its target;
  `merge_projects()` moves all rows onto the target, aliases the source, and logs
  provenance in `project_merges`. Nothing is hard-deleted by a merge.
- **Sessions (Path B):** `upsert_session_summary()` keeps one evolving row per chat;
  `session_records()` / `session_summary()` reload a chat.

### Ranking (`amx/memory/ranking.py` + `amx/config.py`)

`RankingWeights` (0.50 / 0.25 / 0.15 / 0.10, summing to 1.0) and `TYPE_WEIGHTS`
(project_state 1.0 → raw_event 0.3) live in config. Recency uses a 7-day half-life
(`recency_half_life_days`). Keep ranking pure and deterministic — no clocks beyond
`created_at`, no randomness, ties broken by record id.

### Schema & migrations

- `SCHEMA_VERSION = 3` in `schema.py` rides on each record for forward evolution.
- `MIGRATIONS` in `migrations.py` is an ordered list; the applied version lives in
  the `meta` table. Current `db_version 6`.
- **Every migration is additive.** New columns start NULL, new tables start empty,
  so an existing database behaves identically until a host opts into the new
  feature. Don't write a destructive or reordering migration — append a new one.

### Config knobs (env-overridable)

See [WORKING.md §7](WORKING.md#7-configuration) for the user-facing table. In code,
all of them are fields on `AMXConfig` with `os.environ` defaults — `AMX_DB_PATH`,
`AMX_PROFILE_MAX_TOKENS`, `AMX_DIGEST_BUDGET_TOKENS`, `AMX_CHAT_SUMMARY_MAX_TOKENS`,
and the three `AMX_FOUNDRY_IQ_*` variables.

### Adoption (`amx/adoption.py`)

`init()` returns the onboarding payload and AMX's view of setup status (it can't see
the host's files, so "configured?" is answered from a meta marker the host sets via
`amx_init(applied=True)`). `CONTINUITY_INSTRUCTION` is the host-applied rule that
makes models use AMX by default; it's hard-capped under 2000 chars (an over-long
instruction gets ignored) and versioned by `INSTRUCTION_VERSION` so stale installs
can be detected. **If you change the instruction, bump `INSTRUCTION_VERSION`.**

---

## Development workflow

```bash
pip install -e ".[dev]"     # editable install with test deps
pytest                       # the unit suite (186 tests)
python tests/smoke.py        # drives the real server over stdio against a temp DB
```

The smoke test exercises the tool surface end-to-end (27 checks) against a temporary
database via `AMX_DB_PATH` — it never touches your real `~/.amx/amx.db`. **Always
test against a temp DB**, never your real one.

Run the server directly to check it boots:

```bash
python -m amx.mcp.server     # waits for an MCP client on stdin; Ctrl+C to exit
```

### Tests to keep green

The suite covers ingestion, search ranking, token-budget selection, summary
generation, state updates, schema validation, and MCP round-trips. The critical
behaviors: retrieving the right project after a client switch, not over-injecting
context, preserving named decisions, handling cold starts, and ranking ambiguous
searches.

---

## Extending AMX

**Swap the retrieval backend.** All retrieval flows through `search_memory()` in
`amx/memory/retrieval.py`, which today runs FTS5 + the ranking blend. A vector or
hybrid index can replace the FTS5 call behind the same function signature without
touching any tool contract or the ranking interface.

**Add a tool.** Register it in `amx/mcp/tools.py` inside `register_tools()`, wrapped
by the existing `@tool` decorator (which adds the content-free call log). Resolve
identity through `_resolve()`; put any new SQL in `amx/store/sqlite.py`; return a
plain dict or a Pydantic `model_dump()`. Keep the handler thin — validate, resolve,
delegate.

**Change the schema.** Bump `SCHEMA_VERSION`, append an additive migration to
`MIGRATIONS`, and confirm an old database still opens. Document the change.

---

## Contribution guidance

- **Preserve backward compatibility** in tool contracts and memory schemas — clients
  depend on them. Change them only deliberately, and document it.
- **Prefer minimal diffs** over broad rewrites. Don't inflate already-clean files.
- **No abstraction that doesn't materially simplify.** Wrappers, layers, and helpers
  that only feel architecturally nice get removed.
- **Keep retrieval deterministic** where possible — it makes behavior testable.
- **Logs:** concise, structured, and free of secrets and raw sensitive content.
- **Never store secrets, tokens, keys, or PII** in memory, and never commit them.

---

## Repository layout

```text
amx/
  schema.py          typed records + tool payloads (schema-versioned)
  config.py          paths, ranking weights, budgets, env knobs
  cli.py             the `amx` command-line tool
  adoption.py        per-client onboarding (amx_init) + continuity instruction
  clients.py         known MCP clients, detection, and install-mcp registration
  mcp/               FastMCP server + thin tool handlers
  identity/          project_id resolution + duplicate/merge reconciliation
  store/             SQLite (WAL) + FTS5 + migrations
  memory/            ingest, retrieval, ranking, bundle, summary, digest, discovery
  state/             project state + decision log
  integrations/      optional Foundry IQ grounding
  utils/             token estimation, text hashing
tests/               unit suite (186 tests) + smoke.py (stdio round-trip over all tools)
scripts/             install.sh / install.ps1
docs/                WORKING.md, DEV.md, llm_guide.md
```
