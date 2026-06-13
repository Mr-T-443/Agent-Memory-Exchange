"""Manage canonical project state."""

from __future__ import annotations

import json

from amx.memory.ingest import ingest_record
from amx.schema import RecordType
from amx.store import Store


# Retrieve project state.
def get_project_state(store: Store, project_id: str) -> dict:
    return store.get_state(project_id) or {}


# Apply merge patch to state and log changes.
def update_project_state(store: Store, project_id: str, patch: dict) -> dict:
    """Apply a shallow patch to state and record the diff."""
    state = store.get_state(project_id) or {}

    removed = [k for k, v in patch.items() if v is None]
    for key, value in patch.items():
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value

    store.set_state(project_id, state)
    ingest_record(
        store,
        project_id,
        RecordType.RAW_EVENT,
        title="state_update",
        body=json.dumps({"patch": patch, "removed": removed}, sort_keys=True),
    )
    return state
