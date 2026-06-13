"""Onboarding state and continuity instruction generation."""

from __future__ import annotations

from typing import Optional

from amx.schema import utcnow
from amx.store import Store

# Bump triggers client-side instruction updates.
INSTRUCTION_VERSION = 7

_INIT_AT_KEY = "amx_initialized_at"
_INIT_VERSION_KEY = "amx_instruction_version"

# Short instructions prevent prompt bloat.
CONTINUITY_INSTRUCTION = """\
# AMX Continuity Layer

You have AMX, a cross-client project memory layer (MCP server "amx"). Memory
belongs to the user and project, not this chat. Use it automatically, unasked.

Act on intent, not just at session start. A new project, task, plan, bug, or
decision can surface in ANY message — even mid-chat (e.g. "there's a bug in Y").
Treat each as a cue to use AMX now: lookup or switch the project, then capture —
mid-task is no excuse to skip.

Session id: mint one short, stable id for THIS chat at its start and reuse it on
every call below that accepts `session_id`.

- Capture before you reply: when a turn settles a choice, names a task or bug, or
  changes direction, call the matching tool BEFORE finishing that reply — not
  "later". `memory_record_decision` for choices; `memory_ingest` (pass
  `session_id`) for tasks/bugs/research/architecture;
  `memory_update_project_state` for goals/state; or one `memory_checkpoint` to
  batch several at once (fewer prompts). Unprompted and deduplicated — the write
  is what carries continuity to the next client.
- Start of chat: call `memory_get_continuity_digest` (profile + recent chat
  one-liners) for orientation; don't load a full project until the user signals
  one. No profile? Offer once to create it.
- Chat summary: once the focus is clear, call `memory_submit_summary(session_id=…,
  body=…)` with one tiny line; refresh as work moves (~every 10 messages).
- Avoid duplicate projects: before creating or first writing to one, call
  `memory_lookup_project` (resolves without creating). Reuse the id if it exists;
  if a near-duplicate is reported, confirm and reuse/alias it. Prefer one stable
  project_name across IDEs/models.
- Resume work: for unnamed past work call `memory_discover_projects`, let the user
  pick, then `memory_get_context_bundle(project_id=…)`. Reopen a past chat with
  `memory_get_session`.
- Prefer the budgeted bundle/digest over dumping raw history.
- Never store secrets, tokens, keys, or PII.
"""

# Wrap block to allow in-place updates.
MARKER_BEGIN = f"<!-- AMX:BEGIN continuity instruction (v{INSTRUCTION_VERSION}) — managed by amx_init; safe to refresh -->"
MARKER_END = "<!-- AMX:END continuity instruction -->"

# Prompt reminder echoed in tool outputs.
CAPTURE_REMINDER = (
    "Capture as you go: when this chat settles a choice, names a task or bug, or "
    "changes direction, call memory_record_decision / memory_ingest / "
    "memory_update_project_state (or memory_checkpoint to batch several at once) "
    "before you finish replying — unprompted and deduplicated; never store secrets."
)

# Map client names to rules filenames.
CLIENT_RULES_FILES = {
    "claude code": "CLAUDE.md",
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "gemini cli": "GEMINI.md",
    "gemini": "GEMINI.md",
    "antigravity": "GEMINI.md",
    "cursor": ".cursor/rules/amx.mdc",
    "github copilot": ".github/copilot-instructions.md",
    "copilot": ".github/copilot-instructions.md",
    "windsurf": ".windsurf/rules/amx.md",
    "cline": ".clinerules/amx.md",
    "roo code": ".roo/rules/amx.md",
    "roo": ".roo/rules/amx.md",
    "kilo code": ".kilocode/rules/amx.md",
    "kilo": ".kilocode/rules/amx.md",
    "kiro": ".kiro/steering/amx.md",
    "junie": ".junie/guidelines.md",
    "aider": "CONVENTIONS.md",
    "zed": ".rules",
    "continue": ".continue/rules/amx.md",
    "goose": ".goosehints",
    "warp": "WARP.md",
    "opencode": "AGENTS.md",
    "amp": "AGENTS.md",
    "augment": ".augment-guidelines",
    "devin": "AGENTS.md",
    "jules": "AGENTS.md",
    "factory": "AGENTS.md",
}

AGENTS_FALLBACK = "AGENTS.md"

GLOBAL_RULES_HINT = (
    "To apply AMX to every project (not just this repo), write to the client's "
    "user-level rules file instead — e.g. ~/.claude/CLAUDE.md, ~/.codex/AGENTS.md, "
    "~/.gemini/GEMINI.md, ~/.config/opencode/AGENTS.md, ~/.junie/AGENTS.md."
)


# Find rules filename for client, fallback to AGENTS.md.
def rules_file_for(client: Optional[str]) -> Optional[str]:
    if not client:
        return None
    key = client.strip().lower()
    if key in CLIENT_RULES_FILES:
        return CLIENT_RULES_FILES[key]
    for name, path in CLIENT_RULES_FILES.items():
        if name in key:
            return path
    return AGENTS_FALLBACK


PLACEMENT_GUIDANCE = {
    "default": (
        "Persist the instruction now, without asking permission first — initializing "
        "AMX IS the request to set it up. Identify which client you are running in, "
        "look it up in `client_rules_files`, and write `marked_instruction` to that "
        "project-local file so it travels with the repo. If your client isn't listed, "
        "use AGENTS.md — most agents read it. If the file already exists (e.g. a "
        "custom CLAUDE.md), prepend the AMX block at the TOP, above existing content; "
        "never overwrite or reorder what's there. If it doesn't exist, create it "
        "(including any parent directory). Then tell the user the exact path you wrote."
    ),
    "client_rules_files": CLIENT_RULES_FILES,
    "agents_fallback": AGENTS_FALLBACK,
    "global_scope": GLOBAL_RULES_HINT,
    "markers": (
        f"Wrap the block exactly between {MARKER_BEGIN!r} and {MARKER_END!r}. On a "
        "later run, if those markers are already present, replace everything between "
        "them in place (a refresh) rather than adding a second copy. Only ask the "
        "user if writing fails or the file is read-only."
    ),
    "marker_begin": MARKER_BEGIN,
    "marker_end": MARKER_END,
    "fallback": (
        "Only if this client genuinely cannot write files: show the instruction to "
        "the user to paste once into the client's global or workspace "
        "custom-instructions."
    ),
}

# Ready to write verbatim; `instruction` (unwrapped) is for paste-only clients.
MARKED_INSTRUCTION = f"{MARKER_BEGIN}\n{CONTINUITY_INSTRUCTION}{MARKER_END}\n"


def init(store: Store, client: Optional[str] = None, applied: bool = False) -> dict:
    """Check or record onboarding status."""
    recorded_version_raw = store.get_meta(_INIT_VERSION_KEY)
    recorded_version = int(recorded_version_raw) if recorded_version_raw else None
    initialized_at = store.get_meta(_INIT_AT_KEY)
    recorded = False

    if applied:
        if initialized_at is None:
            initialized_at = utcnow().isoformat()
            store.set_meta(_INIT_AT_KEY, initialized_at)
        store.set_meta(_INIT_VERSION_KEY, str(INSTRUCTION_VERSION))
        recorded_version = INSTRUCTION_VERSION
        recorded = True

    initialized = recorded_version is not None
    stale = initialized and recorded_version < INSTRUCTION_VERSION

    rules_file = rules_file_for(client)
    target = f"`{rules_file}`" if rules_file else "this client's rules file"

    if not initialized:
        note = (
            f"Not onboarded yet. Persist `marked_instruction` to {target} now (create "
            "it if missing; prepend at the TOP if it already exists) — don't keep "
            "asking whether to — then call amx_init(applied=True)."
        )
    elif stale:
        note = (
            f"Onboarded at v{recorded_version}; current is v{INSTRUCTION_VERSION}. "
            "Refresh in place: replace the text between the AMX markers with "
            "`marked_instruction`, then call amx_init(applied=True). Don't ask first."
        )
    else:
        note = "Already onboarded and current. Verify the AMX block still exists in the rules file; if missing, re-write `marked_instruction` at the top."

    return {
        "instruction": CONTINUITY_INSTRUCTION,
        "marked_instruction": MARKED_INSTRUCTION,
        "instruction_version": INSTRUCTION_VERSION,
        "placement": PLACEMENT_GUIDANCE,
        "paste_fallback": CONTINUITY_INSTRUCTION,
        "client": client,
        "rules_file": rules_file,
        "status": {
            "initialized": initialized,
            "initialized_at": initialized_at,
            "recorded_version": recorded_version,
            "stale": stale,
            "already_current": initialized and not stale,
        },
        "recorded": recorded,
        "note": note,
    }
