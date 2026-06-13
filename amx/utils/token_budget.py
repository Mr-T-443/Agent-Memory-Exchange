"""Token estimation utilities."""

from __future__ import annotations

_encoder = None
_encoder_loaded = False


# Load tiktoken encoder, falling back to None if package is missing.
def _get_encoder():
    global _encoder, _encoder_loaded
    if not _encoder_loaded:
        _encoder_loaded = True
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder = None
    return _encoder


# Estimate token count using tiktoken or a character-based fallback.
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)
