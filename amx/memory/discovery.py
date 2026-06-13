"""Search and discovery of projects by name or content queries."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from amx.config import AMXConfig
from amx.memory.ranking import rank_records
from amx.schema import DiscoveryResult, ProjectCandidate
from amx.store import Store

_SUMMARY_CHARS = 160


# Resolve display name for project.
def _display_name(project: Optional[dict], project_id: str) -> str:
    if project:
        if project.get("name"):
            return project["name"]
        root = project.get("root_path")
        if root:
            return Path(root).name or project_id
    return project_id


# Generate a brief display summary of the project.
def _one_line_summary(store: Store, project_id: str, fallback_title: str = "") -> str:
    summary = store.latest_summary(project_id)
    if summary and summary.body.strip():
        return " ".join(summary.body.split())[:_SUMMARY_CHARS]
    state = store.get_state(project_id)
    if state and state.get("current_goal"):
        return str(state["current_goal"])
    return fallback_title


# Aggregate record scores into a single project score.
def _aggregate_score(scores: list[float]) -> float:
    """Aggregate search match scores with diminishing returns for extra hits."""
    if not scores:
        return 0.0
    ordered = sorted(scores, reverse=True)
    bonus = sum(s / (2 ** i) * 0.1 for i, s in enumerate(ordered[1:], start=1))
    return round(min(1.0, ordered[0] + bonus), 6)


# Search and rank projects by match query.
def discover_projects(
    store: Store,
    query: Optional[str],
    cfg: AMXConfig,
    limit: Optional[int] = None,
) -> DiscoveryResult:
    limit = limit or cfg.discovery_limit

    if not query or not query.strip():
        return recent_projects(store, cfg, limit)

    hits = store.search_records_global(query, cfg.search_limit)
    if not hits:
        return DiscoveryResult(query=query, candidates=[], note="No projects found.")

    ranked = rank_records(hits, query, cfg)

    # Group matches by project ID.
    hits_by_project: dict[str, list] = {}
    for ranked_record in ranked:
        hits_by_project.setdefault(ranked_record.record.project_id, []).append(ranked_record)

    candidates: list[ProjectCandidate] = []
    for pid, project_hits in hits_by_project.items():
        aggregate_score = _aggregate_score([hit.score for hit in project_hits])
        if aggregate_score < cfg.discovery_score_floor:
            continue
        project = store.get_project(pid)
        candidates.append(
            ProjectCandidate(
                project_id=pid,
                name=_display_name(project, pid),
                score=aggregate_score,
                summary=_one_line_summary(store, pid, project_hits[0].record.title),
                last_activity=store.project_last_activity(pid),
                match_count=len(project_hits),
            )
        )

    candidates.sort(key=lambda c: (-c.score, c.project_id))
    candidates = candidates[:limit]
    note = None if candidates else "No confident match."
    return DiscoveryResult(query=query, candidates=candidates, note=note)


# List projects ordered by recent activity.
def recent_projects(
    store: Store, cfg: AMXConfig, limit: Optional[int] = None
) -> DiscoveryResult:
    limit = limit or cfg.discovery_limit
    rows = store.list_projects_by_activity(limit)
    if not rows:
        return DiscoveryResult(query=None, candidates=[], note="No projects found.")
    candidates = [
        ProjectCandidate(
            project_id=row["project_id"],
            name=_display_name(row, row["project_id"]),
            score=0.0,  # activity listing, not a relevance score
            summary=_one_line_summary(store, row["project_id"]),
            last_activity=row.get("last_activity"),
            match_count=0,
        )
        for row in rows
    ]
    return DiscoveryResult(query=None, candidates=candidates, note=None)
