"""Data models and schemas for AMX records and tool payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 3


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecordType(str, Enum):
    PROJECT_STATE = "project_state"
    DECISION = "decision"
    TASK = "task"
    BUG = "bug"
    RESEARCH = "research"
    ARCHITECTURE = "architecture"
    SUMMARY = "summary"
    ENTITY = "entity"
    THREAD = "thread"
    ARTIFACT_REFERENCE = "artifact_reference"
    RAW_EVENT = "raw_event"


# Active records are open/active; others are kept for search only.
LIFECYCLE_TYPES: frozenset[RecordType] = frozenset({RecordType.TASK, RecordType.BUG})

# Allowed user statuses for tasks/bugs.
SETTABLE_STATUSES: frozenset[str] = frozenset({"open", "done", "resolved", "dropped"})
INACTIVE_STATUSES: frozenset[str] = frozenset({"done", "resolved", "dropped", "superseded"})


# Core persistence record.
class MemoryRecord(BaseModel):
    id: Optional[int] = None
    project_id: str
    type: RecordType
    title: str
    body: str
    entities: list[str] = Field(default_factory=list)
    token_estimate: int = 0
    content_hash: Optional[str] = None  # Deduplication hash.
    status: Optional[str] = None        # Record status (e.g. open, done).
    superseded_by_id: Optional[int] = None  # ID of replacement record.
    session_id: Optional[str] = None    # Session scope.
    schema_version: int = SCHEMA_VERSION
    created_at: datetime = Field(default_factory=utcnow)
    deduped: bool = False  # Indicates if record was deduplicated.


# Recorded project decision.
class Decision(BaseModel):
    id: Optional[int] = None
    project_id: str
    title: str
    rationale: str
    supersedes_id: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)


# Project or session summary.
class Summary(BaseModel):
    id: Optional[int] = None
    project_id: str
    kind: str = "session"
    body: str
    source: str = "client"  # Summary source.
    session_id: Optional[str] = None  # Rolling session identifier.
    created_at: datetime = Field(default_factory=utcnow)


# Search match entry.
class SearchMatch(BaseModel):
    type: str
    title: str
    score: float
    summary: str
    source: str = "local"
    record_id: Optional[int] = None


# Search results container.
class SearchResult(BaseModel):
    query: str
    project_id: str
    budget_tokens: Optional[int] = None
    matches: list[SearchMatch] = Field(default_factory=list)


# Project discovery match candidate.
class ProjectCandidate(BaseModel):
    project_id: str
    name: str           # Display name.
    score: float        # Discovery score.
    summary: str
    last_activity: Optional[str] = None  # Last active timestamp.
    match_count: int = 0


# Discovery search results container.
class DiscoveryResult(BaseModel):
    query: Optional[str] = None
    candidates: list["ProjectCandidate"] = Field(default_factory=list)
    note: Optional[str] = None


# Slice of context payload.
class BundleSlice(BaseModel):
    kind: str  # Slice type.
    title: str
    content: Any
    token_estimate: int


# Context bundle container.
class ContextBundle(BaseModel):
    project_id: str
    budget_tokens: int
    used_tokens: int
    cold_start: bool = False
    slices: list[BundleSlice] = Field(default_factory=list)
    capture_reminder: str = ""  # Reminder prompt.
