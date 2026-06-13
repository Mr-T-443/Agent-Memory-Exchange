"""Tests for store and CLI robustness under edge cases and error states."""

from __future__ import annotations

import threading

import pytest

from amx.config import AMXConfig
from amx.memory.ingest import ingest_record
from amx.schema import RecordType
from amx.store import CorruptDatabaseError, Store


def test_store_usable_from_a_different_thread(tmp_path):
    # Verify database connection can be shared across worker threads.
    store = Store(tmp_path / "amx.db")
    errors: list[str] = []

    def worker():
        try:
            ingest_record(store, "p1", RecordType.TASK, "t", "body")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    store.close()
    assert errors == []


def test_corrupt_database_raises_actionable_error(tmp_path):
    bad = tmp_path / "amx.db"
    bad.write_bytes(b"definitely not a sqlite database" * 50)
    with pytest.raises(CorruptDatabaseError) as exc:
        Store(bad)
    msg = str(exc.value)
    assert "corrupted" in msg
    assert "amx restore" in msg and "amx nukeit" in msg


def test_cli_reports_corrupt_database_cleanly(tmp_path, monkeypatch, capsys):
    from amx.cli import main

    bad = tmp_path / "amx.db"
    bad.write_bytes(b"garbage" * 100)
    monkeypatch.setenv("AMX_DB_PATH", str(bad))
    # Verify CLI reports database corruption without tracebacks.
    assert main(["info"]) == 1
    assert "corrupted" in capsys.readouterr().err


def test_bad_int_env_var_falls_back_to_default(monkeypatch, capsys):
    monkeypatch.setenv("AMX_PROFILE_MAX_TOKENS", "not-a-number")
    cfg = AMXConfig()
    assert cfg.profile_max_tokens == 100  # default, not a crash
    assert "invalid" in capsys.readouterr().err


def test_blank_int_env_var_uses_default(monkeypatch):
    monkeypatch.setenv("AMX_DIGEST_BUDGET_TOKENS", "   ")
    assert AMXConfig().digest_budget_tokens == 100
