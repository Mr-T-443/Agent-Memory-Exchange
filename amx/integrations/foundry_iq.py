"""Optional Foundry IQ grounding search backend."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from amx.config import AMXConfig
from amx.schema import SearchMatch

_API_VERSION = "2024-07-01"
_TIMEOUT_SECONDS = 8
_MAX_RESULTS = 5
_BATCH_SIZE = 100

# Search index fields mapping to the local database table.
_INDEX_FIELDS = [
    {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
    {"name": "project_id", "type": "Edm.String", "filterable": True, "searchable": False},
    {"name": "record_type", "type": "Edm.String", "filterable": True, "searchable": False},
    {"name": "title", "type": "Edm.String", "searchable": True},
    {"name": "content", "type": "Edm.String", "searchable": True},
]


# Send JSON HTTP request to search service.
def _request(url: str, cfg: AMXConfig, body: dict | None = None, method: str = "POST") -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json", "api-key": cfg.foundry_api_key},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def _docs_url(cfg: AMXConfig, op: str) -> str:
    return (
        f"{cfg.foundry_endpoint.rstrip('/')}/indexes/{cfg.foundry_index}"
        f"/docs/{op}?api-version={_API_VERSION}"
    )


# Format a record for index upload.
def _doc(record_id: int, project_id: str, record_type: str, title: str, body: str) -> dict:
    return {
        "@search.action": "mergeOrUpload",
        "id": str(record_id),
        "project_id": project_id,
        "record_type": record_type,
        "title": title,
        "content": body,
    }


# Ensure search index exists and contains all required fields.
def ensure_index(cfg: AMXConfig) -> bool:
    index_url = (
        f"{cfg.foundry_endpoint.rstrip('/')}/indexes/{cfg.foundry_index}"
        f"?api-version={_API_VERSION}"
    )
    try:
        existing = _request(index_url, cfg, method="GET")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        _request(
            index_url, cfg,
            body={"name": cfg.foundry_index, "fields": _INDEX_FIELDS},
            method="PUT",
        )
        return True

    have = {f["name"] for f in existing.get("fields", [])}
    missing = [f for f in _INDEX_FIELDS if f["name"] not in have]
    if missing:
        existing["fields"].extend(missing)
        _request(index_url, cfg, body=existing, method="PUT")
    return True


# Upload database records to search index in batches.
def push_records(rows, cfg: AMXConfig) -> int:
    docs = [_doc(r["id"], r["project_id"], r["type"], r["title"], r["body"]) for r in rows]
    accepted = 0
    for i in range(0, len(docs), _BATCH_SIZE):
        result = _request(_docs_url(cfg, "index"), cfg, body={"value": docs[i : i + _BATCH_SIZE]})
        accepted += sum(1 for r in result.get("value", []) if r.get("status"))
    return accepted


# Push single record, ignoring failures to protect tool calls.
def push_record(
    record_id: int, project_id: str, record_type: str, title: str, body: str, cfg: AMXConfig
) -> None:
    if not cfg.foundry_configured:
        return
    try:
        push_records(
            [{"id": record_id, "project_id": project_id, "type": record_type,
              "title": title, "body": body}],
            cfg,
        )
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass


# Delete records by ID from search index.
def delete_records(ids: list[int], cfg: AMXConfig) -> None:
    if not cfg.foundry_configured or not ids:
        return
    docs = [{"@search.action": "delete", "id": str(i)} for i in ids]
    try:
        for i in range(0, len(docs), _BATCH_SIZE):
            _request(_docs_url(cfg, "index"), cfg, body={"value": docs[i : i + _BATCH_SIZE]})
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        pass


# Fetch all documents from search index.
def fetch_all_docs(cfg: AMXConfig) -> list[dict]:
    docs: list[dict] = []
    skip = 0
    while True:
        data = _request(
            _docs_url(cfg, "search"), cfg,
            body={"search": "*", "top": 1000, "skip": skip,
                  "select": "id,project_id,record_type,title,content"},
        )
        page = data.get("value", [])
        docs.extend(page)
        if len(page) < 1000:
            return docs
        skip += 1000


# Clear all documents from search index.
def clear_all(cfg: AMXConfig) -> int:
    ids = [d["id"] for d in fetch_all_docs(cfg)]
    docs = [{"@search.action": "delete", "id": i} for i in ids]
    for i in range(0, len(docs), _BATCH_SIZE):
        _request(_docs_url(cfg, "index"), cfg, body={"value": docs[i : i + _BATCH_SIZE]})
    return len(ids)


# Normalize raw search scores to 0.5..0.9 range.
def _normalize(scores: list[float]) -> list[float]:
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [0.8] * len(scores)
    return [0.5 + 0.4 * (s - lo) / (hi - lo) for s in scores]


# Search the Foundry IQ index for grounded matches.
def search(query: str, cfg: AMXConfig) -> list[SearchMatch]:
    if not cfg.foundry_configured:
        return []

    try:
        data = _request(
            _docs_url(cfg, "search"), cfg, body={"search": query, "top": _MAX_RESULTS}
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []

    docs = data.get("value", [])
    scores = _normalize([float(d.get("@search.score", 0.0)) for d in docs])

    matches = []
    for doc, score in zip(docs, scores):
        title = str(doc.get("title") or doc.get("name") or doc.get("id") or "Grounded result")
        record_type = doc.get("record_type")
        if record_type:
            title = f"[{record_type}] {title}"
        body = str(doc.get("content") or doc.get("chunk") or doc.get("text") or "")
        matches.append(
            SearchMatch(
                type="grounded",
                title=title,
                score=round(score, 6),
                summary=body[:240],
                source="foundry_iq",
            )
        )
    return matches
