"""Tests for onboarding, client adoption, and instruction management."""

from __future__ import annotations

from amx import adoption


def test_init_cold_returns_payload_and_not_initialized(store):
    result = adoption.init(store)
    assert result["instruction"] == adoption.CONTINUITY_INSTRUCTION
    assert result["instruction_version"] == adoption.INSTRUCTION_VERSION
    assert result["paste_fallback"] == adoption.CONTINUITY_INSTRUCTION
    assert result["placement"]["default"]
    assert result["placement"]["fallback"]
    assert result["recorded"] is False
    assert result["status"]["initialized"] is False
    assert result["status"]["initialized_at"] is None


def test_marked_instruction_wraps_block_for_in_place_refresh(store):
    result = adoption.init(store)
    marked = result["marked_instruction"]
    # Block contains markers and the instruction.
    assert marked.startswith(adoption.MARKER_BEGIN)
    assert marked.rstrip().endswith(adoption.MARKER_END)
    assert adoption.CONTINUITY_INSTRUCTION in marked
    # Markers are exposed for in-place updates.
    assert result["placement"]["marker_begin"] == adoption.MARKER_BEGIN
    assert result["placement"]["marker_end"] == adoption.MARKER_END
    # Version is embedded in the start marker.
    assert str(adoption.INSTRUCTION_VERSION) in adoption.MARKER_BEGIN


def test_rules_file_resolves_known_clients_and_falls_back(store):
    # Map client names to configuration files.
    assert adoption.rules_file_for("claude code") == "CLAUDE.md"
    assert adoption.rules_file_for("Cursor") == ".cursor/rules/amx.mdc"
    assert adoption.rules_file_for("codex") == "AGENTS.md"
    # Match substrings for client names.
    assert adoption.rules_file_for("Windsurf 1.2") == ".windsurf/rules/amx.md"
    # Fall back to standard rules file for unknown clients.
    assert adoption.rules_file_for("some-new-agent") == adoption.AGENTS_FALLBACK
    # Return None when no client is named.
    assert adoption.rules_file_for(None) is None


def test_init_payload_names_the_clients_rules_file(store):
    result = adoption.init(store, client="windsurf")
    assert result["rules_file"] == ".windsurf/rules/amx.md"
    # Expose client rules map.
    assert result["placement"]["client_rules_files"]["cursor"] == ".cursor/rules/amx.mdc"
    assert result["placement"]["agents_fallback"] == "AGENTS.md"
    # Include rules file path in response note.
    assert ".windsurf/rules/amx.md" in result["note"]


def test_init_reading_does_not_mark(store):
    # Verify read-only default for onboarding.
    adoption.init(store)
    assert store.get_meta("amx_initialized_at") is None
    assert adoption.init(store)["status"]["initialized"] is False


def test_applied_records_marker_and_is_idempotent(store):
    first = adoption.init(store, client="claude code", applied=True)
    assert first["recorded"] is True
    assert first["status"]["initialized"] is True
    assert first["status"]["already_current"] is True
    at = first["status"]["initialized_at"]
    assert at is not None

    # Verify initialized status is reported correctly.
    again = adoption.init(store)
    assert again["status"]["initialized"] is True
    assert again["recorded"] is False

    # Verify onboarding is idempotent.
    third = adoption.init(store, applied=True)
    assert third["status"]["initialized_at"] == at


def test_stale_instruction_is_detected(store):
    adoption.init(store, applied=True)
    # Verify stale version detection.
    store.set_meta("amx_instruction_version", str(adoption.INSTRUCTION_VERSION - 1))
    result = adoption.init(store)
    assert result["status"]["initialized"] is True
    assert result["status"]["stale"] is True
    assert result["status"]["already_current"] is False


def test_instruction_is_present_and_bounded(store):
    text = adoption.CONTINUITY_INSTRUCTION
    assert "memory_discover_projects" in text
    assert "secrets" in text
    # Ensure instruction length remains small.
    assert len(text) < 2000


def test_tool_log_is_content_free(store):
    store.record_tool_call("memory_ingest", "p1")
    store.record_tool_call("memory_ingest", "p1")
    store.record_tool_call("amx_init", None)
    counts = store.tool_call_counts()
    assert counts["memory_ingest"] == 2
    assert counts["amx_init"] == 1
