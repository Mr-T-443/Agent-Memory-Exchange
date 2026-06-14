"""CLI command tests."""

from amx import __version__
from amx.cli import build_parser, main


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_info_reports_db_path(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("AMX_DB_PATH", str(tmp_path / "amx.db"))
    assert main(["info"]) == 0
    out = capsys.readouterr().out
    assert "database:" in out
    assert "amx.db" in out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: amx" in capsys.readouterr().out


def test_uninstall_flags_parse():
    args = build_parser().parse_args(["uninstall", "--purge"])
    assert args.purge is True
    assert args.keep_data is False


def test_uninstall_removes_dedicated_venv(monkeypatch, tmp_path, capsys):
    from pathlib import Path

    from amx import cli

    # Isolate HOME directory for testing.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    venv = tmp_path / ".amx-venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "amx").write_text("#!/bin/sh\n")
    monkeypatch.setattr(cli, "_dedicated_venv", lambda: venv)
    # Point to a non-existent database path.
    monkeypatch.setenv("AMX_DB_PATH", str(tmp_path / ".amx" / "amx.db"))

    args = cli.build_parser().parse_args(["uninstall", "--keep-data"])
    assert cli._uninstall(args) == 0
    assert not venv.exists()  # Verify virtualenv is removed.
    assert "venv" in capsys.readouterr().out


def test_dedicated_venv_detected_only_from_that_prefix(monkeypatch, tmp_path):
    import sys as _sys
    from pathlib import Path

    from amx import cli

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Verify prefix mismatch returns None.
    monkeypatch.setattr(_sys, "prefix", str(tmp_path / "usr"))
    assert cli._dedicated_venv() is None
    # Verify prefix match is detected.
    venv = tmp_path / ".amx-venv"
    venv.mkdir()
    monkeypatch.setattr(_sys, "prefix", str(venv))
    assert cli._dedicated_venv() == venv


def test_uninstall_windows_defers_locked_package_removal(monkeypatch, tmp_path, capsys):
    # On Windows the live amx.exe is locked, so pip must NOT run in-process
    # (that half-removes the package). It is handed to a detached child instead.
    from pathlib import Path

    from amx import cli

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_dedicated_venv", lambda: None)
    monkeypatch.setattr(cli, "_pipx_managed", lambda: False)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setenv("AMX_DB_PATH", str(tmp_path / ".amx" / "amx.db"))  # no data dir

    inline, spawned = [], []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, *a, **k: inline.append(cmd) or 0)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: spawned.append(a))

    args = cli.build_parser().parse_args(["uninstall", "--keep-data"])
    assert cli._uninstall(args) == 0
    assert not any("uninstall" in str(c) for c in inline)  # pip never ran inline
    assert len(spawned) == 1  # removal handed to a detached child
    assert "python -m pip uninstall amx" in capsys.readouterr().out


def test_uninstall_non_windows_runs_pip_inline(monkeypatch, tmp_path):
    # Other platforms keep the original in-process uninstall (no self-lock).
    from pathlib import Path

    from amx import cli

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli, "_dedicated_venv", lambda: None)
    monkeypatch.setattr(cli, "_pipx_managed", lambda: False)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setenv("AMX_DB_PATH", str(tmp_path / ".amx" / "amx.db"))

    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, *a, **k: calls.append(cmd) or 0)

    def _no_spawn(*a, **k):
        raise AssertionError("should not spawn a detached child off Windows")

    monkeypatch.setattr(cli.subprocess, "Popen", _no_spawn)

    args = cli.build_parser().parse_args(["uninstall", "--keep-data"])
    assert cli._uninstall(args) == 0
    assert any("uninstall" in str(c) and "amx" in str(c) for c in calls)


def _make_db(path, project_id="p1"):
    from amx.memory.ingest import ingest_record
    from amx.schema import RecordType
    from amx.store import Store

    store = Store(path)
    store.ensure_project(project_id)
    ingest_record(store, project_id, RecordType.TASK, "a task", "body")
    store.close()


def test_backup_creates_timestamped_copy_in_cwd(capsys, monkeypatch, tmp_path):
    db = tmp_path / "amx.db"
    _make_db(db)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setenv("AMX_DB_PATH", str(db))
    monkeypatch.chdir(workdir)

    assert main(["backup"]) == 0
    backups = list(workdir.glob("amx-backup-*.db"))
    assert len(backups) == 1
    assert "1 record(s)" in capsys.readouterr().out


def test_backup_without_database_fails_cleanly(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("AMX_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.chdir(tmp_path)
    assert main(["backup"]) == 1
    assert "Nothing to back up" in capsys.readouterr().out


def test_restore_overwrites_after_yes(capsys, monkeypatch, tmp_path):
    backup = tmp_path / "old.db"
    _make_db(backup, project_id="from-backup")
    current = tmp_path / "amx.db"
    _make_db(current, project_id="current")
    monkeypatch.setenv("AMX_DB_PATH", str(current))

    assert main(["restore", str(backup), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "OVERWRITE" in out and "current:" in out and "incoming:" in out

    from amx.store import Store
    store = Store(current)
    try:
        assert [p["project_id"] for p in store.all_projects()] == ["from-backup"]
    finally:
        store.close()


def test_restore_rejects_non_amx_file(capsys, monkeypatch, tmp_path):
    bogus = tmp_path / "notes.db"
    bogus.write_text("not a database", encoding="utf-8")
    monkeypatch.setenv("AMX_DB_PATH", str(tmp_path / "amx.db"))

    assert main(["restore", str(bogus)]) == 1
    assert "not an AMX database" in capsys.readouterr().out


def test_restore_cancels_without_confirmation(monkeypatch, tmp_path):
    backup = tmp_path / "old.db"
    _make_db(backup, project_id="from-backup")
    current = tmp_path / "amx.db"
    _make_db(current, project_id="current")
    monkeypatch.setenv("AMX_DB_PATH", str(current))

    # Confirm fails when stdin is not a tty.
    assert main(["restore", str(backup)]) == 1

    from amx.store import Store
    store = Store(current)
    try:
        assert [p["project_id"] for p in store.all_projects()] == ["current"]
    finally:
        store.close()
