# AMX — Full Usage Guide

This is the complete guide to using AMX day to day: the ideas behind it, everyday
workflows, real examples, and how to connect it to every major AI coding tool.

New here? Start with the [README](../README.md) for the short version, then come
back when you want the details.

---

## Contents

1. [The ideas behind AMX](#1-the-ideas-behind-amx)
2. [How memory flows](#2-how-memory-flows)
3. [Everyday workflows](#3-everyday-workflows)
4. [Practical examples](#4-practical-examples)
5. [Client setup](#5-client-setup)
6. [Sharing memory across tools — a full day](#6-sharing-memory-across-tools--a-full-day)
7. [Configuration](#7-configuration)
8. [Good habits](#8-good-habits)
9. [Troubleshooting](#9-troubleshooting)
10. [FAQ](#10-faq)
11. [The full tool list](#11-the-full-tool-list)

---

## 1. The ideas behind AMX

A few core ideas explain how AMX works.

### Projects

Every note AMX stores belongs to a **project**. AMX figures out which project you
mean automatically, in this order:

1. An exact project id, if your assistant passes one.
2. A **project name** — `"My App"` becomes `name-my-app`.
3. Your **current folder** — AMX uses the folder's Git remote if there is one,
   otherwise the folder path.
4. Nothing to go on → a shared `default` project.

The same repo lands on the same project from any tool on your machine, and a
**project name** matches even across machines. So GitHub Copilot and Cursor,
opened in the same folder, see the same memory.

> Identity is the *logical project*, not the repository. Because different tools
> pass different hints, one project can occasionally end up under two ids — after
> a rename, a moved folder, or an SSH-vs-HTTPS clone. When that happens you
> *reconcile* them (see [Reconcile a split project](#reconcile-a-split-project)).
> AMX only suggests matches; it never merges without your say-so.

### Records

A **record** is one small, structured note. Records come in types so AMX can rank
them sensibly:

| Type | For |
|---|---|
| `decision` | A choice and why you made it |
| `task` | Something to do (open or done) |
| `bug` | A defect to fix |
| `research` | A finding you looked up |
| `architecture` | A design or structural choice |
| `summary` | A short recap of a session or the project |
| `thread` | A named line of work or topic |
| `entity` | A key thing — a module, service, person |
| `artifact_reference` | A pointer to a file, PR, or doc |
| `raw_event` | Low-level events (AMX logs state changes here) |

Records are **append-only history** — searchable, not edited in place. You curate
them with status changes and corrections rather than rewriting (see
[Keep memory tidy](#keep-memory-tidy)).

### Project state

Separate from records, each project has one **state** document — a small, living
snapshot of where things stand: the current goal, the active task, open issues.
You update it with patches, and AMX logs every change. Think of it as a dashboard,
not a journal.

### The context bundle

When a session starts, your assistant asks for a **context bundle** and AMX
assembles, in priority order:

1. Your profile (who you are)
2. Current project state
3. The latest summary
4. Recent decisions
5. Ranked search matches for whatever you're asking about

…and it **stops once it hits the token budget**. So a new session gets exactly the
relevant slice — a few hundred tokens — instead of a giant history dump.

### Your profile

One short note about *you* (not tied to any project): your stack, your interests,
your current focus. It leads every bundle, so even a bare "Hi" in a fresh tool
already knows who you are. Your assistant can read it, and update it when you ask
("update my profile: I'm focusing on Rust now"). It's surfaced to every connected
tool, so keep secrets out of it.

### Session continuity (per-chat memory)

Beyond projects, AMX tracks individual **chats**. Each conversation gets a stable
session id and a tiny one-line summary that evolves as the chat goes. So your
assistant can offer "continue our last chat" — reloading just that conversation's
thread — separately from loading a whole project. A small **continuity digest** at
the start of a fresh chat shows your profile plus the last few chats' one-liners,
purely for orientation.

### Ranking

Search results are ordered by a fixed formula — no AI, no randomness:

```
score = 0.50 · text relevance
      + 0.25 · recency
      + 0.15 · record-type weight
      + 0.10 · entity overlap
```

Recency fades over about a week; decisions and state outrank incidental mentions.
The same query always returns the same order.

---

## 2. How memory flows

A quick mental model of what happens under the hood when you talk to your AI.

### When you start a session

1. Your assistant asks AMX for a **continuity digest** (profile + recent chats) or
   a **context bundle** (a specific project's state, summary, decisions).
2. AMX pulls those pieces from the local database, ranks what's rankable, and trims
   to the token budget.
3. Your assistant reads that short package and greets you already knowing the
   context.

### When you describe work

1. You say something — a decision, a task, a bug.
2. Your assistant recognizes the intent and calls the right capture tool
   (`memory_record_decision`, `memory_ingest`, `memory_update_project_state`).
3. AMX stores a compact record. If you've said the same thing before, it's
   **deduplicated** — stored once.

### When you search or resume

1. You ask about past work, by name or by vague description.
2. For a vague description, your assistant calls **project discovery**, which scans
   every project and returns ranked candidates; you pick one.
3. For a known project, it loads the bundle directly.
4. **Search** runs a keyword match over that project's records, then applies the
   ranking formula, and returns scored results — the relevant ones on top.

### Context selection (what makes the cut)

When the budget is tight, AMX keeps the highest-priority slices and drops the rest:
state and the latest summary stay; lower-ranked matches get cut. The response
always reports how many tokens it actually used, so nothing is a surprise.

---

## 3. Everyday workflows

You drive all of these with plain language. The tool names are shown only so you
know what's happening.

### Set up AMX in a tool (once per tool)

Say **"set up AMX."** Your assistant calls `amx_init`, saves a short continuity
instruction where the tool loads its rules, and from then on uses memory
automatically. Call it again any time to check status or refresh after an AMX
update. (In a chat-only tool that can't write files, the assistant shows you the
instruction to paste once.)

`amx_init` knows the exact rules file each client auto-loads — the assistant
passes which client it's in and gets back the right path, so it doesn't guess.
Unlisted clients fall back to `AGENTS.md`, which most modern agents read.

| Client | Rules file (project-local) |
|---|---|
| Claude Code | `CLAUDE.md` |
| Codex, opencode, Amp, Devin, Jules, Factory | `AGENTS.md` |
| Gemini CLI / Antigravity | `GEMINI.md` |
| Cursor | `.cursor/rules/amx.mdc` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| Windsurf | `.windsurf/rules/amx.md` |
| Cline | `.clinerules/amx.md` |
| Roo Code | `.roo/rules/amx.md` |
| Kilo Code | `.kilocode/rules/amx.md` |
| Kiro | `.kiro/steering/amx.md` |
| JetBrains Junie | `.junie/guidelines.md` |
| Aider | `CONVENTIONS.md` |
| Zed | `.rules` |
| Continue | `.continue/rules/amx.md` |
| Goose | `.goosehints` |
| Warp | `WARP.md` |
| Augment Code | `.augment-guidelines` |

This is the **rules file** (always-on instructions) — distinct from the **MCP
config** in [§5](#5-client-setup) that registers the server itself. For all your
projects at once, the assistant can write to the user-level file instead (e.g.
`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`).

### Set your profile (once)

> "Set my AMX profile: embedded developer, prefers Rust and Python, currently
> building AMX, into local-first tooling."

Every tool's sessions now open knowing this. Update it the same way whenever your
focus shifts.

### Resume a project (the daily driver)

> "Load this project from AMX and pick up where we left off."

Your assistant gets state, summary, and decisions within budget — no transcript
pasting.

### Find a half-remembered project

> "Continue the browser extension project." — or just "pick up where I left off."

Your assistant calls `memory_discover_projects` (your description in, ranked
candidates out — or recent projects if you give no description), shows the matches,
you pick, and it loads that project's bundle.

### Continue a specific past chat

> "Continue what we were doing in our last conversation."

The continuity digest surfaces recent chats; your assistant reloads the one you
mean with `memory_get_session` — just that chat's summary and thread, not the
whole project.

### Capture as you work (automatic)

Once set up, your assistant records as you go, unprompted. You can also be explicit:

- Made a choice? → "Record a decision: we're using JWT, not sessions, because the
  API is stateless." (`memory_record_decision`)
- Committed to a next step? → captured as a `task`.
- Hit a bug? → "The OTA flow hangs on Windows when the pipe closes." → a `bug`.
- Settled a design? → `architecture`. Looked something up? → `research`.

Capture is deduplicated and best-effort: repeats store once, and if something is
missed, nothing breaks. **Never put secrets, tokens, keys, or PII in memory.**

### Avoid duplicate projects

Before creating or first writing to a project, your assistant calls
`memory_lookup_project` — a read-only check for whether it already exists (handy
when the same work was opened from a different tool). If a near-duplicate turns up,
it reuses or aliases that one instead of starting fresh.

### Keep memory tidy

Memory is curated, not just piled up, so bundles stay current:

- Finished something? → "OAuth is done." / "That bug is fixed." → `memory_update_status`
  marks it done/resolved/dropped. It leaves future bundles but stays searchable.
- A fact changed or was wrong? → "We shipped rollback last week; the note saying
  it's pending is wrong." → `memory_correct` writes the fix and retires the old note.
- Newer info replaces older? → your assistant supersedes the old record.

Bundles inject only active memory; search still returns everything for history.

### Reconcile a split project

Sometimes one project ends up under two ids. When memory seems split:

> "Did my memory get split across two projects?"

Your assistant calls `memory_find_duplicates` (ranked by confidence — a shared Git
remote is near-certain; a shared name or overlapping records are weaker hints). You
confirm the keeper, and it calls `memory_merge_projects` to move everything onto one
id and alias the other. For a rename or a fresh clone with no memory yet,
`memory_alias_project` points the new id at the existing project. Merges are
audit-logged and nothing is deleted.

### End-of-session summary

> "Summarize what we did — open tasks, decisions, blockers — and submit it to AMX."

The next session's bundle leads with this summary. If you forget, AMX builds a
basic one from state + decisions + recent tasks.

### Delete memory (when you really mean it)

Normal hygiene hides things; it doesn't erase them. To truly delete:

- "Delete those records." → `memory_delete` (your assistant confirms first).
- "Erase the whole throwaway-spike project." → `memory_purge_project` (confirms first).
- Wipe *everything* and start over → run `amx nukeit` in a terminal (it asks first).
- Before anything destructive, `amx backup` saves a copy you can `amx restore` later.

These are permanent and not reversible — they exist for cleaning up mistakes or
removing something you didn't mean to store.

---

## 4. Practical examples

These show the actual tool calls and responses behind the scenes. In practice you
just use natural language.

### A fresh project (cold start)

**Call:** `memory_get_context_bundle` with `{"project_name": "billing-service"}`

```json
{
  "project_id": "name-billing-service",
  "budget_tokens": 3000,
  "used_tokens": 0,
  "cold_start": true,
  "slices": [
    {"kind": "project_state", "title": "New project",
     "content": {"project_id": "name-billing-service", "note": "No memory yet."}}
  ]
}
```

`cold_start: true` tells the assistant there's nothing to restore — no error, no noise.

### Setting state and a decision

**Call:** `memory_update_project_state`

```json
{
  "project_name": "billing-service",
  "patch": {
    "current_goal": "Ship usage-based invoicing",
    "active_task": "Implement proration logic",
    "open_issues": ["Webhook retries unhandled", "No idempotency keys"]
  }
}
```

**Call:** `memory_record_decision`

```json
{
  "project_name": "billing-service",
  "title": "Use Stripe metered billing, not custom counters",
  "rationale": "Stripe handles proration edge cases; custom counters risked drift."
}
```

### Resuming the next day

**Call:** `memory_get_context_bundle` with `{"project_name": "billing-service", "budget_tokens": 1000}`

```json
{
  "project_id": "name-billing-service",
  "budget_tokens": 1000,
  "used_tokens": 142,
  "cold_start": false,
  "slices": [
    {"kind": "project_state", "title": "Current project state",
     "content": {"current_goal": "Ship usage-based invoicing",
                 "active_task": "Implement proration logic",
                 "open_issues": ["Webhook retries unhandled", "No idempotency keys"]}},
    {"kind": "summary", "title": "Latest summary",
     "content": "Goal: Ship usage-based invoicing. Active: proration logic. ..."},
    {"kind": "decision", "title": "Use Stripe metered billing, not custom counters",
     "content": {"rationale": "Stripe handles proration edge cases; custom counters risked drift."}}
  ]
}
```

142 tokens of exactly-relevant context instead of a transcript dump.

### Ranked search

**Call:** `memory_search` with `{"query": "webhook retries", "project_name": "billing-service"}`

```json
{
  "query": "webhook retries",
  "project_id": "name-billing-service",
  "matches": [
    {"type": "task", "title": "Handle webhook retry storms", "score": 0.81,
     "summary": "Stripe retries webhooks up to 3 days; dedupe by event id...",
     "source": "local", "record_id": 7},
    {"type": "raw_event", "title": "state_update", "score": 0.34,
     "summary": "{\"patch\": {\"open_issues\": [\"Webhook retries unhandled\"...",
     "source": "local", "record_id": 3}
  ]
}
```

The real task outranks the incidental state-change mention.

### A tiny budget

**Call:** `memory_get_context_bundle` with `{"budget_tokens": 500, "project_name": "billing-service"}`

With a 500-token budget (hotfix scale), the bundle keeps state and the summary,
then cuts whatever doesn't fit. `used_tokens` always reports what was included.

---

## 5. Client setup

AMX is an MCP server every tool launches over **stdio**. The recipe is identical
everywhere — command `python`, args `["-m", "amx.mcp.server"]` — only the config
file format differs.

> **Do it automatically:** `amx install-mcp` detects your installed clients and
> writes the right config for each — no hand-editing. `--list` shows what's
> detected, `--all` registers everywhere, `--client <key>` picks specific ones,
> and `--remove` cleanly unregisters (it only ever touches AMX's own entry). It
> currently auto-configures: **Claude Code, Claude Desktop, Cursor, Windsurf,
> Antigravity, VS Code (GitHub Copilot), Codex, Gemini CLI, opencode, and the
> GitHub Copilot CLI.** The manual recipes below are for anything not on that list,
> or when you'd rather edit the file yourself.

> **Windows:** in JSON, escape backslashes in paths (`"C:\\tools\\amx-venv\\Scripts\\python.exe"`).
> If AMX is in your default Python, plain `"python"` works everywhere.

> **Shared memory:** each tool runs its own server process against the same
> `~/.amx/amx.db` (SQLite WAL mode), so memory is shared with no central daemon.

> **Virtual environments:** if you installed AMX in a venv, point `command` at that
> venv's Python (full path), since the client launches the server itself.

### 5.1 Claude Code

**Project scope** (recommended; commit-able). Create `.mcp.json` in the project
root — this repo ships one you can copy:

```json
{
  "mcpServers": {
    "amx": { "command": "python", "args": ["-m", "amx.mcp.server"] }
  }
}
```

**User scope** (all your projects):

```bash
claude mcp add amx --scope user -- python -m amx.mcp.server
```

Restart Claude Code and approve the server. Tools appear as `mcp__amx__...`. Then
say "set up AMX." Claude Code's own memory features don't conflict — AMX is what
travels to *other* tools. Use `/mcp` to check status.

### 5.2 Cursor

`.cursor/mcp.json` in the project, or `~/.cursor/mcp.json` globally:

```json
{
  "mcpServers": {
    "amx": { "command": "python", "args": ["-m", "amx.mcp.server"] }
  }
}
```

Settings → MCP should show the server green with its tools. The agent decides when
to call them; you can also direct it explicitly.

### 5.3 GitHub Copilot (VS Code)

Needs VS Code 1.99+, a Copilot subscription, and **Agent mode**. Create
`.vscode/mcp.json` — note the key is `servers`, not `mcpServers`:

```json
{
  "servers": {
    "amx": { "type": "stdio", "command": "python", "args": ["-m", "amx.mcp.server"] }
  }
}
```

Open Copilot Chat → switch to **Agent** mode → confirm the `amx` tools are listed.
MCP tools only work in Agent mode (not plain chat).

### 5.4 OpenAI Codex CLI

`~/.codex/config.toml` (TOML, not JSON; note the underscore in `mcp_servers`):

```toml
[mcp_servers.amx]
command = "python"
args = ["-m", "amx.mcp.server"]
```

Start `codex`; it launches the server and lists tools. Codex sandboxes commands by
default; DB writes go to your home directory, so use `--full-auto` or approve
writes if the sandbox blocks them.

### 5.5 Gemini CLI

`~/.gemini/settings.json` (global) or `.gemini/settings.json` (project):

```json
{
  "mcpServers": {
    "amx": { "command": "python", "args": ["-m", "amx.mcp.server"], "timeout": 15000 }
  }
}
```

Run `gemini`, then `/mcp` to list connected servers and their tools.

### 5.6 Cline (VS Code extension)

Cline icon → **MCP Servers** → **Configure MCP Servers** (opens `cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "amx": {
      "command": "python",
      "args": ["-m", "amx.mcp.server"],
      "disabled": false,
      "autoApprove": ["memory_get_context_bundle", "memory_search", "memory_get_project_state"]
    }
  }
}
```

`autoApprove` lets read-only tools run without a prompt; keep write tools on manual
approval if you want oversight. Mention AMX in your `.clinerules` to make use
systematic.

### 5.7 Roo Code (VS Code extension)

MCP icon → **Edit MCP Settings** (`mcp_settings.json`), or project `.roo/mcp.json`:

```json
{
  "mcpServers": {
    "amx": {
      "command": "python",
      "args": ["-m", "amx.mcp.server"],
      "alwaysAllow": ["memory_get_context_bundle", "memory_search"]
    }
  }
}
```

Same model as Cline. Roo's modes pair well with AMX: Architect mode reads the
bundle and records decisions; Code mode updates the active task as it works.

### 5.8 Continue.dev

Add to `~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: amx
    command: python
    args:
      - -m
      - amx.mcp.server
```

MCP tools are available in **Agent mode**. Open the tools dropdown in the Continue
chat to confirm `amx` is connected.

### 5.9 Other clients

Same recipe, different config location:

| Client | Config location | Key |
|---|---|---|
| **Windsurf (Codeium)** | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |
| **Zed** | `settings.json` → `context_servers` | `command`/`args` |
| **Claude Desktop** | `claude_desktop_config.json` | `mcpServers` |
| **JetBrains AI Assistant** | Settings → Tools → AI Assistant → MCP | UI form |

Universal stdio recipe: command `python`, args `["-m", "amx.mcp.server"]`, optional
env `{"AMX_DB_PATH": "..."}`.

> **Desktop/web tools** don't have a project folder, so name the project in your
> prompt: "Get the AMX bundle for project_name 'billing-service'."

---

## 6. Sharing memory across tools — a full day

One project (`~/code/billing-service`, on GitHub), three tools, one thread.

**9:00 — GitHub Copilot.** You start the invoicing feature.

> "Load AMX context for this project." → resolves the same id from the Git remote →
> cold start, fresh project.

Over two hours, Copilot records the goal and active task in state, the Stripe
decision, and a "handle webhook retry storms" task. Before lunch: "Submit a session
summary to AMX."

**13:00 — Cursor, same repo.** Different tool, zero shared config beyond the MCP entry.

> "Restore project memory from AMX and continue." → same folder → same Git remote →
> **same project** → the bundle returns the morning's state, summary, and the Stripe
> decision. Cursor continues the proration work knowing exactly why Stripe was chosen,
> updating the active task as it goes.

**18:00 — Gemini CLI, quick check.**

> "Get the AMX bundle for project_name 'billing-service'. What's still open?" → the
> bundle shows Cursor's updates and the unresolved webhook task.

What made it work: all three launched the same server against the same database;
the project resolved identically from each; and no tool ever saw another's
transcript — only compact, ranked, budgeted slices.

---

## 7. Configuration

AMX works with **zero configuration** — first use creates `~/.amx/amx.db`. Every
setting is an optional environment variable:

| Variable | Default | Purpose |
|---|---|---|
| `AMX_DB_PATH` | `~/.amx/amx.db` | Where the database lives |
| `AMX_PROFILE_MAX_TOKENS` | `100` | Max size of your profile |
| `AMX_DIGEST_BUDGET_TOKENS` | `100` | Budget for the cold-start continuity digest |
| `AMX_CHAT_SUMMARY_MAX_TOKENS` | `30` | Max size of a per-chat mini-summary |
| `AMX_FOUNDRY_IQ_ENDPOINT` | *(unset)* | Optional grounded-search endpoint |
| `AMX_FOUNDRY_IQ_API_KEY` | *(unset)* | API key for the above |
| `AMX_FOUNDRY_IQ_INDEX` | *(unset)* | Index name to query |
| `AMX_FOUNDRY_SYNC` | `false` | Auto-mirror writes/deletes to the index (set by `amx enable-foundry`) |

Copy `.env.example` to `.env` as a starting point. **Never commit `.env`** — the
`.gitignore` already excludes it.

**Optional grounded search (Foundry IQ):** when all three `AMX_FOUNDRY_IQ_*`
variables are set, `memory_search` also queries that knowledge index and merges
results (tagged `source: "foundry_iq"`) alongside local memory. Unset or failing,
AMX silently returns local-only results — it never blocks on the network. The
easiest setup is `amx enable-foundry`: it prompts for missing keys, tests the
connection, creates or upgrades the index, uploads existing memory, and turns
on auto-sync so every write and delete mirrors to the index from then on
(`memory_delete`, `memory_purge_project`, and `amx nukeit` clean up the index
too). `amx foundry-sync` reconciles the index to match local memory exactly;
`amx local-sync` restores local records *from* the index (e.g. on a new
machine). Keys live in `~/.amx/.env`, which the server loads automatically.

---

## 8. Good habits

- **Capture decisions, not conversations.** One decision with a clear rationale
  beats fifty ingested chat lines, and it ranks high and survives summarization.
- **Keep state lean.** State goes into *every* bundle — treat it like a dashboard.
  Move finished items out.
- **Summarize long sessions.** A good written summary beats the automatic fallback.
- **Right-size budgets.** ~500 tokens for a hotfix, ~1000 for a one-file change,
  ~3000 (default) for feature work, ~5000 for architecture sessions.
- **Pick one project name and stick to it**, especially across machines.
- **Auto-approve reads, review writes.** In tools with per-tool approval, whitelist
  `memory_get_context_bundle`, `memory_search`, `memory_get_project_state`; keep
  write tools on confirmation until you trust the assistant's judgment.
- **Never store secrets.** No keys, tokens, passwords, customer data, or PII — the
  database is plain SQLite on your disk, and memory reaches every connected tool.

---

## 9. Troubleshooting

**Server won't start / "failed to connect."** Test the import:
`python -c "from amx.mcp.server import create_server; print('OK')"`. A
`ModuleNotFoundError` means the client is launching a different Python than the one
AMX is installed in — use the absolute path to the right interpreter in your config.
On Windows, check backslash escaping in JSON paths. Some GUI tools don't inherit
your shell `PATH`; absolute interpreter paths fix that.

**Tools connect but never get called.** Confirm you're in **Agent mode** (Copilot,
Continue). Prompt explicitly the first time ("use the amx MCP tools to…"); once the
assistant has seen them work, it uses them more readily.

**Two tools see different memory for the same repo.** They resolved different
projects — one used a name, the other a folder; or the Git remote changed; or you
renamed/moved/re-cloned the folder. Standardize on one identity hint, then
*reconcile* the split (ask the assistant to find duplicate projects and confirm a
merge). Check the `project_id` in any response to see what each resolved.

**Search returns nothing.** Search is per-project — verify the `project_id` matches
where you stored things. It matches whole words without stemming ("ranked" won't
match "ranking"); add synonyms to the query.

**"Database is locked."** Rare under WAL mode. Transient locks resolve on retry; if
it persists, kill any stale `python -m amx.mcp.server` process holding a connection.

**Bundle is missing things I stored.** Compare `used_tokens` to `budget_tokens` —
the budget cut lower-priority slices. Raise the budget, or pass a `query` so ranking
pulls the right matches into the top slots.

**`amx nukeit` says the database is in use.** A running server (launched by an IDE
or CLI) holds the file open. Close all AMX clients and IDEs, then retry.

**Where's my data / start fresh.** Everything is in `~/.amx/amx.db` (plus `-wal` /
`-shm` sidecars). Run `amx backup` to drop a timestamped, self-contained copy in the
current directory (safe even while a server is running), and `amx restore <file.db>`
to load one back — it shows what would be overwritten and asks first. Delete the
database, or run `amx nukeit`, to reset — AMX recreates it on next launch.

**`pip install amx` fails / "externally-managed-environment".** AMX isn't on PyPI,
so `pip install amx` is always wrong. Install from the repo with **pipx**
(`pipx install git+https://github.com/Mr-T-443/Agent-Memory-Exchange.git`) or, with
no pipx, a **virtualenv** — `python3 -m venv ~/.amx-venv && ~/.amx-venv/bin/pip
install git+…`. Don't use `--break-system-packages`. The one-liner installer offers
both (default pipx); force one with `AMX_INSTALL_METHOD=pipx|venv`.

**`amx` isn't on my PATH.** With a venv install, call it by full path
(`~/.amx-venv/bin/amx …`) or add `~/.local/bin` to your PATH. MCP clients are
unaffected either way — `amx install-mcp` writes an absolute interpreter path into
each client config, so the server launches regardless of your shell PATH.

**A client still lists `amx` after I uninstalled.** Current versions auto-strip
AMX's entry from every detected client on `amx uninstall`. If a stale entry predates
that (it points at a binary that no longer exists), reinstall AMX and run
`amx install-mcp --remove --all`, which surgically removes only AMX's entry and
leaves your other servers untouched.

---

## 10. FAQ

**Does AMX send my data anywhere?** No. It's a local server and a local SQLite
file. The only network call is the optional Foundry IQ search, and only if you
configure it.

**Does AMX call an LLM?** Never. Summaries are written by your tool's AI and
submitted to AMX; AMX's own fallback summary is purely mechanical. This keeps the
memory layer cheap, fast, and deterministic.

**Can several tools use AMX at once?** Yes. Each launches its own server process;
all share the database safely via SQLite WAL mode.

**How do I share memory between two machines?** Not built in yet. Today: sync
`~/.amx/amx.db` yourself (e.g. a synced folder via `AMX_DB_PATH`) and use
project-name identity so ids match across machines.

**Why keyword search instead of embeddings?** Determinism, zero dependencies, and
it works well at this scale. Retrieval lives behind one function, so a vector
backend could replace it later without changing any tool.

**How big does the database get?** Records are tiny (usually under 1 KB). Thousands
per project is megabytes, not gigabytes.

**Can I edit or delete a record?** Normal hygiene *hides* records (status, correct,
supersede) but keeps them searchable. To truly erase, use `memory_delete` /
`memory_purge_project` (both confirm first), or `amx nukeit` for everything. These
are permanent.

**Is my memory safe across AMX upgrades?** Yes — the database is versioned and
migrations apply automatically on startup; updates never touch your data.

---

## 11. The full tool list

AMX exposes **25 tools**. Names use underscores (an MCP naming rule) and map 1:1 to
the spec's dotted names (`memory_ingest` ↔ `memory.ingest`).

**Setup & profile**

| Tool | What it does |
|---|---|
| `amx_init` | Onboard AMX into the current tool; returns the continuity instruction and where to save it |
| `memory_get_profile` | Read your profile (call at session start when there's no project) |
| `memory_set_profile` | Replace your profile (empty string clears it) |

**Orientation & resuming**

| Tool | What it does |
|---|---|
| `memory_get_continuity_digest` | Tiny cold-start digest: profile + recent chats' one-liners |
| `memory_discover_projects` | Find a project across all projects from a description (or list recent) |
| `memory_lookup_project` | Read-only check whether a project already exists (avoids duplicates) |
| `memory_get_session` | Reload one past chat's summary and thread |
| `memory_get_context_bundle` | Budgeted context pack for a project — the daily driver |

**Capture**

| Tool | What it does |
|---|---|
| `memory_ingest` | Capture a typed record (task, bug, research, architecture, …); deduplicated |
| `memory_record_decision` | Log a decision + rationale (also searchable) |
| `memory_update_project_state` | Patch project state (shallow merge; `null` removes a key) |
| `memory_submit_summary` | Store a written summary — a project checkpoint, or a per-chat mini-summary |

**Read**

| Tool | What it does |
|---|---|
| `memory_search` | Ranked matches (plus Foundry IQ when configured) |
| `memory_get_project_state` | Read canonical project state |
| `memory_get_summary` | Latest summary, or a mechanical fallback |
| `memory_list_decisions` | The decision log, newest first |
| `memory_list_threads` | Recent threads |

**Tidy up**

| Tool | What it does |
|---|---|
| `memory_update_status` | Mark a task/bug done/resolved/dropped (or reopen) |
| `memory_correct` | Replace a wrong record with a corrected one in one step |
| `memory_supersede` | Retire an older record in favor of a newer existing one |

**Reconcile**

| Tool | What it does |
|---|---|
| `memory_find_duplicates` | Read-only: find projects that look like the same one under different ids |
| `memory_merge_projects` | Merge one project's memory into another (after you confirm) |
| `memory_alias_project` | Point an alternate id at a canonical project (rename / moved / re-clone) |

**Delete (permanent — confirm first)**

| Tool | What it does |
|---|---|
| `memory_delete` | Hard-delete specific records (`confirm=True` required) |
| `memory_purge_project` | Wipe an entire project (`confirm=True` required) |

Every project-scoped tool accepts optional `project_id`, `project_name`, and `cwd`
for identity. The profile, discovery, digest, and `amx_init` tools are
instance-level and take none.
