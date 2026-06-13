"""Continuity digest for fresh sessions before a project is loaded."""

from __future__ import annotations

from amx.adoption import CAPTURE_REMINDER
from amx.config import AMXConfig
from amx.store import Store
from amx.utils.token_budget import estimate_tokens


# Build a digest of the user profile and recent chat summaries.
def build_continuity_digest(
    store: Store, cfg: AMXConfig, budget_tokens: int | None = None
) -> dict:
    """Get the user profile and budgeted list of recent session summaries."""
    budget = budget_tokens or cfg.digest_budget_tokens
    profile = store.get_profile()
    used = 0
    clues: list[dict] = []

    for summary in store.recent_session_summaries(limit=50):
        body = summary.body.strip()
        if not body:
            continue
        est = estimate_tokens(body)
        # Always include at least one summary.
        if clues and used + est > budget:
            break
        clues.append(
            {
                "session_id": summary.session_id,
                "project_id": summary.project_id,
                "summary": body,
                "updated_at": summary.created_at.isoformat(),
            }
        )
        used += est

    return {
        "profile": profile["text"] if profile else None,
        "budget_tokens": budget,
        "used_tokens": used,
        "recent_chats": clues,
        "capture_reminder": CAPTURE_REMINDER,
        "note": (
            "Orientation only — recent chats, not a loaded project. "
            "Use memory_discover_projects / memory_get_context_bundle to go deeper."
        ),
    }
