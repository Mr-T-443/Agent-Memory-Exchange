"""Detect potential duplicate projects based on name, remote URL, path, or content."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from amx.config import AMXConfig
from amx.identity.project_id import _slug, normalize_git_remote
from amx.store import Store


def _basename(root_path: Optional[str]) -> Optional[str]:
    if not root_path:
        return None
    return Path(root_path).name.lower() or None


# Compare two projects to estimate likeness.
def _signal(a: str, b: str, info_a: dict, info_b: dict, entities, hashes) -> tuple[float, str]:
    if info_a["remote"] and info_b["remote"] and info_a["remote"] == info_b["remote"]:
        return 0.95, "git_remote"
    if info_a["name_slug"] and info_b["name_slug"] and info_a["name_slug"] == info_b["name_slug"]:
        return 0.8, "name"
    if info_a["basename"] and info_b["basename"] and info_a["basename"] == info_b["basename"]:
        if entities(a) & entities(b):
            return 0.6, "path+entity"
    if hashes(a) & hashes(b):
        return 0.5, "content_overlap"
    return 0.0, ""


def find_duplicate_projects(
    store: Store, cfg: AMXConfig, project_id: Optional[str] = None
) -> dict:
    """Find potential duplicate projects based on shared properties."""
    focus = store.canonical_project_id(project_id) if project_id else None

    # Skip projects that are already aliased to another.
    projects = []
    for project in store.all_projects():
        pid = project["project_id"]
        if store.canonical_project_id(pid) == pid:
            projects.append(project)

    # Pre-compute metadata for comparison.
    info: dict[str, dict] = {}
    for project in projects:
        pid = project["project_id"]
        info[pid] = {
            "name_slug": _slug(project["name"]) if project.get("name") else None,
            "remote": normalize_git_remote(project["git_remote"]) if project.get("git_remote") else None,
            "basename": _basename(project.get("root_path")),
            "count": store.record_count(pid),
        }

    ids = [project["project_id"] for project in projects]

    _entities: dict[str, set] = {}
    _hashes: dict[str, set] = {}

    def entities(pid: str) -> set:
        if pid not in _entities:
            _entities[pid] = store.project_entities(pid)
        return _entities[pid]

    def hashes(pid: str) -> set:
        if pid not in _hashes:
            _hashes[pid] = store.project_content_hashes(pid)
        return _hashes[pid]

    pairs: list[dict] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if focus and focus not in (a, b):
                continue
            confidence, signal = _signal(a, b, info[a], info[b], entities, hashes)
            if confidence <= 0:
                continue
            # Prefer the project with more records as the canonical one.
            canonical = a if info[a]["count"] >= info[b]["count"] else b
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "confidence": confidence,
                    "signal": signal,
                    "suggested_canonical": canonical,
                }
            )

    pairs.sort(key=lambda p: (-p["confidence"], p["a"], p["b"]))
    if not focus:
        pairs = pairs[: cfg.discovery_limit]

    note = None if pairs else "No likely duplicates found."
    return {"focus": focus, "pairs": pairs, "note": note}
