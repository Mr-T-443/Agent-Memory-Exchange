"""Command line interface for AMX operations."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.error

from amx import __version__
from amx.config import AMXConfig
from amx.store import CorruptDatabaseError

# Default update source.
DEFAULT_UPDATE_SOURCE = "git+https://github.com/Mr-T-443/Agent-Memory-Exchange.git"


# Check if running inside a pipx environment.
def _pipx_managed() -> bool:
    from pathlib import Path

    parts = Path(sys.prefix).parts
    return "pipx" in parts and "venvs" in parts


# Path to the dedicated virtualenv.
def _dedicated_venv_path():
    from pathlib import Path

    return Path.home() / ".amx-venv"


# Get the dedicated virtualenv path if running within it.
def _dedicated_venv():
    from pathlib import Path

    venv = _dedicated_venv_path()
    try:
        return venv if Path(sys.prefix).resolve() == venv.resolve() else None
    except OSError:
        return None


# Remove the dedicated virtualenv and launcher symlinks.
def _remove_dedicated_venv(venv) -> None:
    from pathlib import Path

    bin_dirs = [Path.home() / ".local" / "bin", venv / "bin", venv / "Scripts"]
    for d in bin_dirs:
        for name in ("amx", "amx-server", "amx.exe", "amx-server.exe"):
            link = d / name
            try:
                if link.is_symlink() and venv in link.resolve().parents:
                    link.unlink()
            except OSError:
                pass
    shutil.rmtree(venv, ignore_errors=True)
    if venv.exists():
        print(f"Removed amx, but could not delete the venv at {venv} (files in use).")
        print(f"Finish by removing it manually: {venv}")
    else:
        print(f"Removed amx and its venv at {venv}.")


def _run_server(_args) -> int:
    from amx.mcp.server import main as server_main

    server_main()
    return 0


def _version(_args) -> int:
    print(f"amx {__version__}")
    return 0


def _info(_args) -> int:
    cfg = AMXConfig()
    print(f"amx {__version__}")
    print(f"database: {cfg.db_path}")
    print(f"exists:   {cfg.db_path.exists()}")

    if cfg.db_path.exists():
        import sqlite3

        from amx.store import Store

        try:
            store = Store(cfg.db_path)
            try:
                projects = store.all_projects()
                print(f"projects: {len(projects)}")
            finally:
                store.close()
        except sqlite3.DatabaseError:
            print("projects: UNREADABLE - the database file is corrupted.")
            print("Recover with 'amx restore <backup.db>' or reset with 'amx nukeit'.")

    if not cfg.foundry_configured:
        print("foundry:  off")
    elif cfg.foundry_sync_enabled:
        print("foundry:  configured (sync on)")
    else:
        print("foundry:  configured (sync off; run 'amx enable-foundry' to activate)")
    return 0


def _update(args) -> int:
    source = args.source or DEFAULT_UPDATE_SOURCE
    before = __version__
    print(f"Updating amx from {source!r} (current {before})...")
    if _pipx_managed():
        pipx = shutil.which("pipx")
        if not pipx:
            print("AMX was installed with pipx, but pipx isn't on PATH.")
            print(f"Install pipx, then run: pipx install --force {source}")
            return 1
        code = subprocess.call([pipx, "install", "--force", source])
    else:
        code = subprocess.call(
            [sys.executable, "-m", "pip", "install", "--upgrade", source]
        )
    if code != 0:
        print("Update failed. Your installation and data are unchanged.")
        return code
    print("Done. Your database and configuration were preserved.")
    return 0


def _uninstall(args) -> int:
    cfg = AMXConfig()

    # Unregister client configurations before removing the package.
    try:
        from amx import clients as clients_mod

        cleaned = [
            client["label"]
            for client in clients_mod.known_clients()
            if clients_mod.is_detected(client) and clients_mod.uninstall(client)
        ]
        if cleaned:
            print(f"Unregistered AMX from: {', '.join(cleaned)}.")
    except Exception:
        pass  # config cleanup is best-effort; continue to remove the package

    venv = _dedicated_venv()
    if venv:
        # Dedicated venv: drop the whole thing rather than just the package.
        _remove_dedicated_venv(venv)
    elif _pipx_managed():
        pipx = shutil.which("pipx")
        if not pipx:
            print("AMX was installed with pipx, but pipx isn't on PATH.")
            print("Install/locate pipx, then run: pipx uninstall amx")
            return 1
        code = subprocess.call([pipx, "uninstall", "amx"])
        if code != 0:
            return code
    else:
        code = subprocess.call([sys.executable, "-m", "pip", "uninstall", "-y", "amx"])
        if code != 0:
            return code

    data_dir = cfg.db_path.parent
    if not data_dir.exists():
        print("Removed amx. No local data directory to clean up.")
        return 0

    remove = args.purge or (not args.keep_data and _confirm(
        f"Delete your memory database at {data_dir}? [y/N] "
    ))
    if remove:
        shutil.rmtree(data_dir, ignore_errors=True)
        print(f"Removed amx and deleted {data_dir}.")
        if cfg.foundry_configured and cfg.foundry_sync_enabled:
            _clear_foundry_best_effort(cfg)
    else:
        print(f"Removed amx. Kept your data at {data_dir}.")
    return 0


# Format a one-line metadata summary for a database file.
def _db_metadata(path) -> str:
    import datetime
    import sqlite3

    stat = path.stat()
    size_kb = stat.st_size / 1024
    modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        finally:
            conn.close()
        counts = f"{projects} project(s), {records} record(s)"
    except sqlite3.Error:
        counts = "not a readable AMX database"
    return f"{size_kb:,.0f} KB, modified {modified}, {counts}"


def _backup(_args) -> int:
    import datetime
    import sqlite3
    from pathlib import Path

    cfg = AMXConfig()
    if not cfg.db_path.exists():
        print(f"No database at {cfg.db_path}. Nothing to back up.")
        return 1

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path.cwd() / f"amx-backup-{stamp}.db"

    # Safely copy SQLite database.
    src = sqlite3.connect(cfg.db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        # Disable WAL mode on backup database.
        dst.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.DatabaseError:
        dst.close()
        src.close()
        dest.unlink(missing_ok=True)
        print(f"Cannot back up: {cfg.db_path} is corrupted or not a database.")
        print("Recover with 'amx restore <backup.db>' or reset with 'amx nukeit'.")
        return 1
    finally:
        dst.close()
        src.close()

    print(f"Backed up to {dest}")
    print(f"  {_db_metadata(dest)}")
    print(f"Restore later with: amx restore {dest.name}")
    return 0


def _restore(args) -> int:
    import sqlite3
    from pathlib import Path

    cfg = AMXConfig()
    source = Path(args.path)
    if not source.is_file():
        print(f"No file at {source}.")
        return 1

    # Validate backup database before restoring.
    try:
        conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            conn.execute("SELECT value FROM meta WHERE key='db_version'").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        print(f"{source} is not an AMX database. Nothing changed.")
        return 1

    target = cfg.db_path
    if target.exists():
        print(f"This will OVERWRITE your current memory at {target}")
        print(f"  current: {_db_metadata(target)}")
        print(f"  incoming: {_db_metadata(source)}")
        if not (args.yes or _confirm("Replace the current database? [y/N] ")):
            print("Cancelled. Nothing changed.")
            return 1

    # Remove current database and WAL files.
    sidecars = [target, target.with_name(target.name + "-wal"),
                target.with_name(target.name + "-shm")]
    for f in sidecars:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            print("The database is in use - close all AMX clients and IDEs "
                  "(so no `amx server` process is running), then try again.")
            return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"Restored {source.name} to {target}")
    print(f"  {_db_metadata(target)}")
    cfg = AMXConfig()
    if cfg.foundry_configured and cfg.foundry_sync_enabled:
        print("Foundry IQ sync is on; run 'amx foundry-sync' to realign the index.")
    return 0


def _nukeit(args) -> int:
    cfg = AMXConfig()
    files = [
        cfg.db_path,
        cfg.db_path.with_name(cfg.db_path.name + "-wal"),
        cfg.db_path.with_name(cfg.db_path.name + "-shm"),
    ]

    if not cfg.db_path.exists():
        print(f"No database at {cfg.db_path}. Nothing to nuke.")
        return 0

    projects = "?"
    try:
        from amx.store import Store

        store = Store(cfg.db_path)
        try:
            projects = len(store.all_projects())
        finally:
            store.close()
    except Exception:
        pass

    wipe_foundry = cfg.foundry_configured and cfg.foundry_sync_enabled
    print(f"!! This permanently deletes ALL AMX memory at {cfg.db_path}")
    print(f"!! {projects} project(s) and every record/decision/summary will be lost.")
    if wipe_foundry:
        print("!! Foundry IQ sync is on: the Azure index will be emptied too.")
    if not (args.yes or _confirm("Type 'y' to nuke everything [y/N] ")):
        print("Aborted. Nothing was deleted.")
        return 1

    locked = []
    for f in files:
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            locked.append(f)

    if locked:
        print("Could not delete the database - it is in use by another process,")
        print("likely an AMX server your IDE or CLI launched. Close all AMX clients")
        print("and IDEs (so no `amx server` process is running), then try again.")
        return 1

    print(f"Nuked. {cfg.db_path} is gone; AMX recreates an empty database on next launch.")
    if wipe_foundry:
        _clear_foundry_best_effort(cfg)
    return 0


# Clear Foundry index on local database reset.
def _clear_foundry_best_effort(cfg: AMXConfig) -> None:
    from amx.integrations import foundry_iq

    try:
        removed = foundry_iq.clear_all(cfg)
        print(f"Foundry IQ index emptied ({removed} document(s) removed).")
    except Exception as e:
        print(f"Could not empty the Foundry IQ index ({e}). "
              "Run 'amx foundry-sync' later to reconcile it.")


def _enable_foundry(_args) -> int:
    from amx.config import _set_env_file_key

    cfg = AMXConfig()

    if not cfg.foundry_configured:
        if not sys.stdin.isatty():
            print("Foundry IQ keys are not set. Add these to ~/.amx/.env first:")
            print("  AMX_FOUNDRY_IQ_ENDPOINT=https://<service>.search.windows.net")
            print("  AMX_FOUNDRY_IQ_API_KEY=<key>")
            print("  AMX_FOUNDRY_IQ_INDEX=<index-name>")
            print("Then re-run: amx enable-foundry")
            return 1
        print("Foundry IQ credentials not found. Enter them now (saved to ~/.amx/.env):")
        endpoint = input("  Endpoint (https://<service>.search.windows.net): ").strip()
        api_key  = input("  API key: ").strip()
        index    = input("  Index name [amx-memory]: ").strip() or "amx-memory"
        if not endpoint or not api_key:
            print("Endpoint and API key are required.")
            return 1
        _set_env_file_key("AMX_FOUNDRY_IQ_ENDPOINT", endpoint)
        _set_env_file_key("AMX_FOUNDRY_IQ_API_KEY",   api_key)
        _set_env_file_key("AMX_FOUNDRY_IQ_INDEX",      index)
        os.environ["AMX_FOUNDRY_IQ_ENDPOINT"] = endpoint
        os.environ["AMX_FOUNDRY_IQ_API_KEY"]   = api_key
        os.environ["AMX_FOUNDRY_IQ_INDEX"]      = index
        cfg = AMXConfig()

    from amx.integrations import foundry_iq

    print("Testing Foundry IQ connection...")
    try:
        foundry_iq.ensure_index(cfg)
        print("  Connection OK; index ready.")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:200]
        print(f"  Connection failed: HTTP {e.code} - {msg}")
        if sys.stdin.isatty() and _confirm("Re-enter credentials? [y/N] "):
            for key in ("AMX_FOUNDRY_IQ_ENDPOINT", "AMX_FOUNDRY_IQ_API_KEY", "AMX_FOUNDRY_IQ_INDEX"):
                os.environ.pop(key, None)
            return _enable_foundry(_args)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  Connection failed: {e}")
        return 1

    code = _foundry_sync(_args)
    if code != 0:
        return code
    _set_env_file_key("AMX_FOUNDRY_SYNC", "true")
    print("Foundry IQ sync enabled. New writes and deletes sync automatically.")
    return 0


def _foundry_sync(_args) -> int:
    from amx.integrations import foundry_iq
    from amx.store import Store

    cfg = AMXConfig()
    if not cfg.foundry_configured:
        print("Foundry IQ is not configured. Run 'amx enable-foundry' first.")
        return 1

    store = Store(cfg.db_path)
    rows = store.export_records()
    store.close()

    try:
        foundry_iq.ensure_index(cfg)
        pushed = foundry_iq.push_records(rows, cfg) if rows else 0
        local_ids = {str(r["id"]) for r in rows}
        orphans = [d["id"] for d in foundry_iq.fetch_all_docs(cfg) if d["id"] not in local_ids]
        if orphans:
            foundry_iq.delete_records(orphans, cfg)
    except urllib.error.HTTPError as e:
        print(f"Sync failed: HTTP {e.code} - {e.read().decode('utf-8', 'replace')[:200]}")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"Sync failed: {e}")
        return 1

    print(f"Foundry IQ now mirrors local memory: {pushed} record(s) pushed, "
          f"{len(orphans)} stale document(s) removed.")
    return 0


def _local_sync(_args) -> int:
    from amx.integrations import foundry_iq
    from amx.memory.ingest import ingest_record
    from amx.schema import RecordType
    from amx.store import Store

    cfg = AMXConfig()
    if not cfg.foundry_configured:
        print("Foundry IQ is not configured. Run 'amx enable-foundry' first.")
        return 1

    try:
        docs = foundry_iq.fetch_all_docs(cfg)
    except urllib.error.HTTPError as e:
        print(f"Fetch failed: HTTP {e.code} - {e.read().decode('utf-8', 'replace')[:200]}")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"Fetch failed: {e}")
        return 1

    if not docs:
        print("Foundry IQ index is empty. Nothing to restore.")
        return 0

    valid_types = {t.value for t in RecordType}
    store = Store(cfg.db_path)
    restored = skipped = already = 0
    try:
        for doc in docs:
            project_id = doc.get("project_id")
            record_type = doc.get("record_type")
            title = doc.get("title") or ""
            # Skip legacy or malformed documents.
            if not project_id or record_type not in valid_types:
                skipped += 1
                continue
            store.ensure_project(project_id)
            result = ingest_record(
                store, project_id, RecordType(record_type), title, doc.get("content") or ""
            )
            if result.deduped:
                already += 1
            else:
                restored += 1
    finally:
        store.close()

    print(f"Restored {restored} record(s) from Foundry IQ "
          f"({already} already present, {skipped} skipped).")
    if skipped:
        print("Skipped documents predate AMX's current index format; "
              "run 'amx foundry-sync' from a machine that still has them locally.")
    if restored:
        print("Note: restored records have new local ids. Run 'amx foundry-sync' "
              "to realign the index with this database.")
    return 0


def _install_mcp(args) -> int:
    from amx import clients as clients_mod

    all_clients = clients_mod.known_clients()
    detected = [c for c in all_clients if clients_mod.is_detected(c)]

    if args.list:
        for client in all_clients:
            mark = "detected" if client in detected else "not found"
            status = clients_mod.trust_status(client)
            trust = "auto-approve:on" if status else (
                "auto-approve:off" if status is False else "auto-approve:ui")
            print(f"{client['key']:<16} {mark:<10} {trust:<18} {client['config']}")
        return 0

    if args.client:
        wanted = {key.strip() for arg in args.client for key in arg.split(",")}
        unknown = wanted - {c["key"] for c in all_clients}
        if unknown:
            print(f"Unknown client(s): {', '.join(sorted(unknown))}. "
                  f"Valid: {', '.join(c['key'] for c in all_clients)}")
            return 1
        targets = [c for c in all_clients if c["key"] in wanted]
    elif args.all:
        targets = detected
    elif sys.stdin.isatty():
        if not detected:
            print("No known AI clients detected on this machine.")
            print("Use 'amx install-mcp --client <name>' to write a config anyway:")
            print(f"  {', '.join(c['key'] for c in all_clients)}")
            return 1
        print("Detected AI clients:")
        for i, client in enumerate(detected, 1):
            print(f"  {i}. {client['label']}")
        verb = "Remove AMX from" if args.remove else "Install AMX into"
        choice = input(f"{verb} which? (numbers like 1,3 / all) [all]: ").strip()
        if not choice or choice.lower() == "all":
            targets = detected
        else:
            try:
                picks = [int(p) for p in choice.replace(",", " ").split()]
                if not picks or any(p < 1 or p > len(detected) for p in picks):
                    raise ValueError
                targets = [detected[p - 1] for p in picks]
            except ValueError:
                print("Invalid selection. Nothing changed.")
                return 1
    else:
        print("Not a terminal. Use --all for all detected clients, or --client <name>.")
        return 1

    if not targets:
        print("Nothing selected. No configs changed.")
        return 0

    if args.remove:
        removed = 0
        for client in targets:
            try:
                path = clients_mod.uninstall(client)
                if path:
                    print(f"  {client['label']}: removed AMX from {path}")
                    removed += 1
                else:
                    print(f"  {client['label']}: no AMX entry found")
            except Exception as e:
                print(f"  {client['label']}: FAILED - {e}")
        print("Done." if removed else "Nothing to remove.")
        return 0

    failures = 0
    for client in targets:
        try:
            path = clients_mod.install(client)
            print(f"  {client['label']}: registered AMX in {path}")
        except Exception as e:
            failures += 1
            print(f"  {client['label']}: FAILED - {e}")

    _apply_trust(args, targets, clients_mod)

    if failures == 0:
        print("Done. Restart the client(s) so they pick up the AMX server.")
    return 1 if failures else 0


# Apply auto-approval settings.
def _apply_trust(args, targets, clients_mod) -> None:
    trust = True if args.trust else (False if args.no_trust else None)

    if trust is None:
        if sys.stdin.isatty():
            print()
            print("AMX can auto-approve its own tools so the client stops asking")
            print("permission on every memory call. AMX only writes to your local")
            print("memory database (no code execution, no network).")
            answer = input("Auto-approve AMX's tools where supported? [y/N] ").strip().lower()
            trust = answer in ("y", "yes")
        else:
            print("Tip: re-run with --trust to auto-approve AMX's tools and stop the")
            print("per-call permission prompts ('amx install-mcp --list' shows the state).")
            return

    print()
    for client in targets:
        path = clients_mod.set_trust(client, trust)
        if path is not None:
            print(f"  {client['label']}: auto-approve {'ON' if trust else 'OFF'}  ({path})")
        elif trust:
            print(f"  {client['label']}: {clients_mod.trust_hint(client)}")


def _disable_foundry(_args) -> int:
    from amx.config import _set_env_file_key

    _set_env_file_key("AMX_FOUNDRY_SYNC", "false")
    print("Foundry IQ sync disabled. Local memory is unchanged; Azure index is untouched.")
    return 0


# Prompt for confirmation.
def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amx", description="Agent Memory Exchange")
    parser.add_argument("-V", "--version", action="version", version=f"amx {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("server", help="Run the AMX MCP server (stdio).").set_defaults(func=_run_server)
    sub.add_parser("version", help="Print the AMX version.").set_defaults(func=_version)
    sub.add_parser("info", help="Show database location and health.").set_defaults(func=_info)

    update = sub.add_parser("update", help="Upgrade AMX (preserves your data).")
    update.add_argument("--source", help="Install source (default: the AMX repo).")
    update.set_defaults(func=_update)

    uninstall = sub.add_parser("uninstall", help="Remove AMX (asks before deleting data).")
    uninstall.add_argument("--purge", action="store_true", help="Also delete the database, no prompt.")
    uninstall.add_argument("--keep-data", action="store_true", help="Keep the database, no prompt.")
    uninstall.set_defaults(func=_uninstall)

    sub.add_parser(
        "backup",
        help="Copy your memory database into the current directory (timestamped).",
    ).set_defaults(func=_backup)

    restore = sub.add_parser(
        "restore",
        help="Replace your memory database with a backup file (asks before overwriting).",
    )
    restore.add_argument("path", help="Path to the backup .db file.")
    restore.add_argument("--yes", action="store_true", help="Skip the overwrite prompt.")
    restore.set_defaults(func=_restore)

    nukeit = sub.add_parser("nukeit", help="Wipe ALL memory (keeps AMX installed; asks first).")
    nukeit.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    nukeit.set_defaults(func=_nukeit)

    sub.add_parser(
        "enable-foundry",
        help="Enable auto-sync to Foundry IQ (uploads existing records, then syncs all future writes).",
    ).set_defaults(func=_enable_foundry)

    sub.add_parser(
        "disable-foundry",
        help="Disable auto-sync to Foundry IQ (local memory is unaffected).",
    ).set_defaults(func=_disable_foundry)

    sub.add_parser(
        "foundry-sync",
        help="Make Foundry IQ mirror local memory (push all records, remove stale docs).",
    ).set_defaults(func=_foundry_sync)

    sub.add_parser(
        "local-sync",
        help="Restore local records from Foundry IQ (safe to re-run; deduplicated).",
    ).set_defaults(func=_local_sync)

    install_mcp = sub.add_parser(
        "install-mcp",
        help="Register the AMX MCP server in installed AI clients (Claude Code, Cursor, ...).",
    )
    install_mcp.add_argument("--list", action="store_true",
                             help="List known clients and whether they were detected.")
    install_mcp.add_argument("--client", action="append",
                             help="Install for a specific client key (repeat or comma-separate).")
    install_mcp.add_argument("--all", action="store_true",
                             help="Install for every detected client, no prompt.")
    install_mcp.add_argument("--trust", action="store_true",
                             help="Auto-approve AMX's tools so the client stops prompting per call.")
    install_mcp.add_argument("--no-trust", action="store_true",
                             help="Turn auto-approve off (register only, or undo a prior --trust).")
    install_mcp.add_argument("--remove", action="store_true",
                             help="Remove AMX's entry from the selected clients (preserves other servers).")
    install_mcp.set_defaults(func=_install_mcp)

    return parser


# Prevent encoding crashes on legacy Windows consoles.
def _harden_console_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # not reconfigurable (e.g. already redirected under pytest)


def main(argv: list[str] | None = None) -> int:
    _harden_console_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except CorruptDatabaseError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
