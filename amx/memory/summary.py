"""Retrieve, submit, or generate extractive project summaries."""

from __future__ import annotations

from amx.schema import RecordType, Summary
from amx.store import Store


def submit_summary(
    store: Store, project_id: str, body: str, kind: str = "session"
) -> Summary:
    summary = Summary(project_id=project_id, kind=kind, body=body, source="client")
    summary.id = store.insert_summary(summary)
    return summary


def build_extractive_summary(store: Store, project_id: str) -> Summary | None:
    lines: list[str] = []

    state = store.get_state(project_id)
    if state:
        goal = state.get("current_goal")
        task = state.get("active_task")
        if goal:
            lines.append(f"Goal: {goal}")
        if task:
            lines.append(f"Active task: {task}")
        for issue in state.get("open_issues", [])[:5]:
            lines.append(f"Open issue: {issue}")

    # Use only active items for auto-injection.
    decisions = store.list_decisions(project_id, limit=3, exclude_superseded=True)
    for decision in decisions:
        lines.append(f"Decision: {decision.title}")

    tasks = store.recent_records(project_id, limit=3, type=RecordType.TASK, active_only=True)
    for task in tasks:
        lines.append(f"Task: {task.title}")

    if not lines:
        return None
    return Summary(project_id=project_id, body="\n".join(lines), source="extractive")


def get_or_build_summary(store: Store, project_id: str) -> Summary | None:
    summary = store.latest_summary(project_id)
    if summary is not None:
        return summary
    return build_extractive_summary(store, project_id)
