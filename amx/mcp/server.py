"""AMX MCP server entrypoint (stdio transport)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from amx.config import AMXConfig
from amx.mcp.tools import register_tools
from amx.store import Store


def create_server(cfg: AMXConfig | None = None) -> FastMCP:
    cfg = cfg or AMXConfig()
    store = Store(cfg.db_path)
    mcp = FastMCP(
        "amx",
        instructions=(
            "AMX is a cross-client project memory layer. If AMX is not yet set up "
            "in this client, call amx_init once (offer it to the user first) and "
            "follow its guidance to persist the continuity instruction — that is "
            "what makes the behaviors below reliable. On session start, call "
            "memory_get_context_bundle (pass cwd or project_name) to restore "
            "context — it opens with the user's profile. If there is no project "
            "context (e.g. the user just says hi), call memory_get_profile "
            "instead; if no profile exists, offer once to create one. Update the "
            "profile via memory_set_profile when the user asks. When the user "
            "wants to continue work but hasn't named the project, call "
            "memory_discover_projects (pass their description, or nothing for "
            "recent projects), show the candidates, let the user pick, then load "
            "it with memory_get_context_bundle(project_id=...). As you work, "
            "capture memory without being asked: record choices with "
            "memory_record_decision; capture tasks, bugs, research findings, and "
            "architecture choices with memory_ingest (types task, bug, research, "
            "architecture); keep state current with memory_update_project_state; "
            "and submit a short memory_submit_summary at natural checkpoints. "
            "Capture is best-effort and deduplicated — never store secrets, "
            "tokens, keys, or PII."
        ),
    )
    register_tools(mcp, store, cfg)
    return mcp


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
