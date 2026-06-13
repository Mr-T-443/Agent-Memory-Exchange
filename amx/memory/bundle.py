"""Build a token-budgeted context bundle for LLM injection."""

from __future__ import annotations

import json

from amx.adoption import CAPTURE_REMINDER
from amx.config import AMXConfig
from amx.memory.retrieval import search_memory
from amx.memory.summary import get_or_build_summary
from amx.schema import BundleSlice, ContextBundle, RecordType
from amx.store import Store
from amx.utils.token_budget import estimate_tokens


class _Builder:
    def __init__(self, budget: int, margin: float):
        self.effective = int(budget * (1.0 - margin))
        self.used = 0
        self.slices: list[BundleSlice] = []

    # Return False and skip slice if token budget is exhausted.
    def add(self, kind: str, title: str, content) -> bool:
        text = content if isinstance(content, str) else json.dumps(content)
        est = estimate_tokens(text)
        if self.slices and self.used + est > self.effective:
            return False
        self.slices.append(
            BundleSlice(kind=kind, title=title, content=content, token_estimate=est)
        )
        self.used += est
        return True


def build_bundle(
    store: Store,
    project_id: str,
    cfg: AMXConfig,
    query: str | None = None,
    budget_tokens: int | None = None,
) -> ContextBundle:
    budget = budget_tokens or cfg.default_budget_tokens
    builder = _Builder(budget, cfg.bundle_safety_margin)

    profile = store.get_profile()
    if profile is not None:
        builder.add("user_profile", "User profile", profile["text"])

    state = store.get_state(project_id)
    cold_start = state is None and store.record_count(project_id) == 0

    if cold_start:
        return ContextBundle(
            project_id=project_id,
            budget_tokens=budget,
            used_tokens=builder.used,
            cold_start=True,
            capture_reminder=CAPTURE_REMINDER,
            slices=builder.slices
            + [
                BundleSlice(
                    kind="project_state",
                    title="New project",
                    content={"project_id": project_id, "note": "No memory yet."},
                    token_estimate=0,
                )
            ],
        )

    if state is not None:
        builder.add("project_state", "Current project state", state)

    summary = get_or_build_summary(store, project_id)
    if summary:
        builder.add("summary", f"Latest summary ({summary.source})", summary.body)

    # Include only active, non-superseded decisions.
    for decision in store.list_decisions(
        project_id, cfg.max_bundle_decisions, exclude_superseded=True
    ):
        if not builder.add("decision", decision.title, {"rationale": decision.rationale}):
            break

    # Skip decision records as they are included above.
    if query:
        result = search_memory(store, project_id, query, limit=10, cfg=cfg, active_only=True)
        for match in result.matches:
            if match.type == RecordType.DECISION.value:
                continue
            if not builder.add(
                "match",
                match.title,
                {"type": match.type, "score": match.score, "summary": match.summary},
            ):
                break
    else:
        for record in store.recent_records(project_id, limit=10, active_only=True):
            if record.type == RecordType.DECISION:
                continue
            if not builder.add(
                "record",
                record.title,
                {"type": record.type.value, "body": record.body},
            ):
                break

    return ContextBundle(
        project_id=project_id,
        budget_tokens=budget,
        used_tokens=builder.used,
        cold_start=False,
        capture_reminder=CAPTURE_REMINDER,
        slices=builder.slices,
    )
