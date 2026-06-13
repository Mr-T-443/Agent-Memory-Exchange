"""Ingest and deduplicate typed memory records."""

from __future__ import annotations

from amx.schema import MemoryRecord, RecordType
from amx.store import Store
from amx.utils.text import content_hash
from amx.utils.token_budget import estimate_tokens


def ingest_record(
    store: Store,
    project_id: str,
    type: RecordType,
    title: str,
    body: str,
    entities: list[str] | None = None,
    session_id: str | None = None,
) -> MemoryRecord:
    digest = content_hash(type.value, title, body)

    existing = store.find_record_by_hash(project_id, digest)
    if existing is not None:
        existing.deduped = True
        return existing

    record = MemoryRecord(
        project_id=project_id,
        type=type,
        title=title,
        body=body,
        entities=entities or [],
        token_estimate=estimate_tokens(title + "\n" + body),
        content_hash=digest,
        session_id=session_id,
    )
    record.id = store.insert_record(record)
    return record
