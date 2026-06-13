"""Text normalization and hashing utilities."""

from __future__ import annotations

import hashlib
import re

_WS_RE = re.compile(r"\s+")


# Normalize text by folding case and collapsing whitespace.
def normalize(text: str) -> str:
    return _WS_RE.sub(" ", text).strip().casefold()


# Generate stable deduplication hash for record content.
def content_hash(type_value: str, title: str, body: str) -> str:
    payload = "\x1f".join(normalize(p) for p in (type_value, title, body))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
