"""Retrieve and rank memory matches from local and grounding search databases."""

from __future__ import annotations

from amx.config import AMXConfig
from amx.integrations import foundry_iq
from amx.memory.ranking import rank_records
from amx.schema import SearchMatch, SearchResult
from amx.store import Store

_SNIPPET_CHARS = 240


def _snippet(body: str) -> str:
    body = " ".join(body.split())
    if len(body) <= _SNIPPET_CHARS:
        return body
    return body[:_SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"


def search_memory(
    store: Store,
    project_id: str,
    query: str,
    limit: int,
    cfg: AMXConfig,
    active_only: bool = False,
) -> SearchResult:
    hits = store.search_records(project_id, query, cfg.search_limit, active_only=active_only)
    ranked = rank_records(hits, query, cfg)

    matches = []
    for ranked_record in ranked:
        record = ranked_record.record
        matches.append(
            SearchMatch(
                type=record.type.value,
                title=record.title,
                score=ranked_record.score,
                summary=store.fts_snippet(record.id, query) or _snippet(record.body),
                source="local",
                record_id=record.id,
            )
        )

    if cfg.foundry_configured:
        matches.extend(foundry_iq.search(query, cfg))

    matches.sort(key=lambda m: -m.score)
    return SearchResult(query=query, project_id=project_id, matches=matches[:limit])
