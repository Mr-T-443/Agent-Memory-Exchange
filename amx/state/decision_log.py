"""Record and list project decisions."""

from __future__ import annotations

from amx.memory.ingest import ingest_record
from amx.schema import Decision, RecordType
from amx.store import Store


# Store decision and write it as a memory record.
def record_decision(
    store: Store,
    project_id: str,
    title: str,
    rationale: str,
    supersedes_id: int | None = None,
) -> Decision:
    decision = Decision(
        project_id=project_id,
        title=title,
        rationale=rationale,
        supersedes_id=supersedes_id,
    )
    decision.id = store.insert_decision(decision)
    ingest_record(store, project_id, RecordType.DECISION, title=title, body=rationale)
    return decision


# Retrieve recent decisions.
def list_decisions(store: Store, project_id: str, limit: int = 10) -> list[Decision]:
    return store.list_decisions(project_id, limit)
