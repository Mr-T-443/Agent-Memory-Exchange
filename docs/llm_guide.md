# AMX Setup Guide (for AI assistants)

You are an AI assistant helping a user install, set up, update, or uninstall
**AMX** — a local memory layer that connects to AI coding tools over MCP. Follow
the relevant section below. Prefer running commands for the user (with their
approval) over telling them to do it manually. Confirm before anything destructive.

AMX is **local-first**: all memory lives in one SQLite file (default `~/.amx/amx.db`).
Installs and updates never touch that file.

---

## Install

1. **Check Python.** AMX needs Python 3.10+.

   ```bash
   python --version    # or: python3 --version  / py --version  (Windows)
   ```

   If it's older than 3.10 or missing, ask the user to install Python 3.10+ first.

2. **Install AMX.** AMX is **not published on PyPI** — never run `pip install amx`
   (that pulls an unrelated package). Always install from this repository. Pick the
   method that fits the user's situation:

   - **Guided one-liner (also handles Foundry IQ + client registration):** it
     auto-picks an install method, asking pipx-vs-venv only when both are available
     (default pipx). Force one non-interactively with `AMX_INSTALL_METHOD=venv` (or
     `pipx`). The venv install lands at `~/.amx-venv` and `amx uninstall` removes it
     cleanly.

     ```bash
     # Linux / macOS
     curl -fsSL https://raw.githubusercontent.com/Mr-T-443/Agent-Memory-Exchange/main/scripts/install.sh | sh
     ```

     ```powershell
     # Windows (PowerShell)
     irm https://raw.githubusercontent.com/Mr-T-443/Agent-Memory-Exchange/main/scripts/install.ps1 | iex
     ```

   - **Direct install with pipx (recommended manual method):** isolated, on PATH,
     and it works on externally-managed Pythons where plain `pip install` is
     blocked with `error: externally-managed-environment` (PEP 668).

     ```bash
     pipx install git+https://github.com/Mr-T-443/Agent-Memory-Exchange.git
     ```

   - **From a local checkout of this repo:** `pipx install .` (or `pip install .`
     inside a virtualenv).

   - **No pipx, and the user doesn't want it:** use a virtualenv — it works on
     externally-managed systems too, and needs nothing on PATH.

     ```bash
     python3 -m venv ~/.amx       # Windows: py -m venv %USERPROFILE%\.amx
     ~/.amx/bin/pip install git+https://github.com/Mr-T-443/Agent-Memory-Exchange.git
     ```

     Then drive AMX by full path: `~/.amx/bin/amx install-mcp --all`,
     `~/.amx/bin/amx update`, `~/.amx/bin/amx uninstall`. `install-mcp` writes that
     venv's Python into each client config, so the server launches without PATH
     changes. (On a non-managed system, `pip install --user git+...` also works.)

   **If a plain `pip install` fails with `externally-managed-environment`:** don't
   use `--break-system-packages`. Use the virtualenv recipe above, or install pipx
   (`sudo apt install pipx`, `brew install pipx`, or
   `python3 -m pip install --user pipx`) and use the pipx command. The one-liner
   installers offer a dedicated venv automatically when pipx is absent.

   All methods install the `amx` CLI and MCP server and leave any existing memory
   untouched. Re-running upgrades in place.

3. **Verify.**

   ```bash
   amx info
   ```

   This prints the version and the database path. If `amx` isn't found on PATH,
   the same command works as `python -m amx.cli info`, and the install output
   shows which scripts directory to add to PATH.

4. **Register AMX with the user's AI tool.** The fast path is the built-in
   installer — it detects which AI clients exist on this machine and writes the
   right config for each one:

   ```bash
   amx install-mcp --list    # see what's detected (Claude Code, Cursor, Codex, ...)
   amx install-mcp --all     # register AMX in every detected client, no prompts
   amx install-mcp --client cursor,codex   # or pick specific ones
   ```

   Prefer this over hand-editing configs. If a client isn't covered, register it
   manually: AMX runs as an MCP server that the tool launches over stdio with

   ```bash
   amx server          # equivalently: python -m amx.mcp.server
   ```

   The most common config shape (Claude Code, Cursor, most others) is:

   ```json
   {
     "mcpServers": {
       "amx": {
         "command": "python",
         "args": ["-m", "amx.mcp.server"]
       }
     }
   }
   ```

   - **Claude Code:** this repo ships a `.mcp.json` with exactly this — if the user
     is in the repo, just have them restart Claude Code. Otherwise add it to their
     `.mcp.json` or run `claude mcp add amx --scope user -- python -m amx.mcp.server`.
   - **Other tools:** the config file location and key differ per tool. See the
     "Client setup" section of `docs/WORKING.md` for Cursor, GitHub Copilot, Codex,
     Gemini CLI, Cline, Roo Code, Continue.dev, and more.
   - If AMX was installed into a virtual environment, point `command` at that
     environment's Python (its full path), because the client launches the server
     itself.

5. **Tell the user to restart their AI tool** so it picks up the new MCP server.

---

## First-time setup

After AMX is connected, do two things to make it useful:

1. **Onboard AMX in this client.** Call the `amx_init` tool, **passing `client`**
   (e.g. `"claude code"`, `"cursor"`, `"codex"`, `"windsurf"`) — the one you're
   running in. The payload then resolves `rules_file` to the exact file that client
   auto-loads as always-on context, so you don't have to guess. It also returns the
   "continuity instruction" (both plain and as `marked_instruction`, wrapped in
   idempotency markers) and the full per-client map under
   `placement.client_rules_files`. Just write it — don't keep asking the user
   whether to. Create that rules file if it's missing (including any parent
   directory); if a custom one already exists, prepend `marked_instruction` at the
   **top**, above the existing content, without overwriting or reordering it. Then
   call `amx_init(applied=True)` once to record that it's done. On a later run, if
   the markers are already in the file, replace what's between them in place rather
   than adding a second copy. Only ask the user if the write fails or the file is
   read-only — or, in a client that genuinely can't write files, show the
   instruction and have them paste it into their tool's custom instructions once.

   **Which file each client loads** (unlisted clients → `AGENTS.md`, which most
   modern agents read):

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

   For **all projects** instead of one repo, write to the client's user-level file
   (e.g. `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `~/.gemini/GEMINI.md`).

2. **Create the user's profile (optional but recommended).** Offer once to set a
   short profile describing who they are and what they work on. For example:

   > "Embedded developer, prefers Rust and Python, building AMX, likes local-first
   > tooling."

   Save it with `memory_set_profile`. It leads every future session so any tool
   knows who the user is. Keep it short; never put secrets or sensitive data in it.

That's it. From here, the user just talks normally and you capture decisions,
tasks, and progress as you work.

---

## Update

```bash
amx update
```

Upgrades AMX in place and preserves the memory database. `amx update` detects how
AMX was installed — pipx, a dedicated `~/.amx-venv`, or plain pip — and uses the
right mechanism automatically, so the same command works everywhere. (Equivalents,
if running it by hand: pipx → `pipx install --force git+…`; venv →
`~/.amx-venv/bin/pip install -U git+…`.) From a local checkout, run
`amx update --source .`. Confirm the new version with `amx info`.

---

## Backup and restore

`amx backup` writes a timestamped, self-contained copy of the memory database
into the current directory (safe while a server is running). `amx restore
<file.db>` replaces the current database with a backup — it validates the file,
shows the metadata of both databases, and asks before overwriting (`--yes` to
skip the prompt; only pass it after the user has confirmed). Offer a backup
before anything destructive (`amx nukeit`, `amx restore`, purges).

## Uninstall

Ask the user whether they want to keep their memory database first, then run the
matching command:

```bash
amx uninstall              # removes AMX; asks before deleting the memory database
amx uninstall --keep-data  # removes AMX, keeps the memory database
amx uninstall --purge      # removes AMX and the memory database, no prompt
```

`amx uninstall` cleans up after itself: it strips AMX's entry from every detected
client config (only its own entry — other servers are left intact) and, for a
dedicated `~/.amx-venv` install, removes the whole venv too. To unregister from
clients without removing the package, use `amx install-mcp --remove --all`.

**Note:** `amx uninstall` removes the *software*. To wipe *memory* but keep AMX
installed, use `amx nukeit` instead (it asks for confirmation first).
