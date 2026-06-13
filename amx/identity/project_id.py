"""Resolve project identity from identifier, git remote, or working directory."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_MULTISLASH_RE = re.compile(r"/+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# Normalize SSH/HTTPS git remote URLs to host/path.
def normalize_git_remote(url: str) -> str:
    s = url.strip()
    if not s:
        return s

    if _SCHEME_RE.match(s) is None and "@" in s and ":" in s:
        # Parse scp-like Git URL.
        userhost, _, path = s.partition(":")
        host = userhost.partition("@")[2] or userhost
        rest = f"{host}/{path}"
    else:
        rest = _SCHEME_RE.sub("", s)
        if "@" in rest:  # Strip credentials.
            rest = rest.split("@", 1)[1]

    rest = _MULTISLASH_RE.sub("/", rest.replace("\\", "/")).rstrip("/")
    if rest.endswith(".git"):
        rest = rest[:-4]
    return rest.lower()


def _git_remote(cwd: str) -> Optional[str]:
    # Detach stdin to prevent blocking git on Windows.
    try:
        proc = subprocess.Popen(
            ["git", "-C", cwd, "config", "--get", "remote.origin.url"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return None

    try:
        out, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        # Terminate process on timeout.
        proc.kill()
        return None

    url = out.strip()
    return url or None


def resolve_project_id(
    project_id: Optional[str] = None,
    project_name: Optional[str] = None,
    cwd: Optional[str] = None,
) -> tuple[str, Optional[str], Optional[str]]:
    if project_id:
        return project_id, None, None

    if project_name:
        return f"name-{_slug(project_name)}", None, None

    if cwd:
        root = str(Path(cwd).resolve())
        remote = _git_remote(root)
        if remote:
            # Resolve from Git remote.
            return f"git-{_short_hash(remote)}", root, remote
        return f"path-{_short_hash(root.lower())}", root, None

    return "default", None, None
