"""AI client auto-configuration and MCP registration."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional


# Resolved lazily for testing.
def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))


# OS-specific application support directory.
def _app_support(name: str) -> Path:
    if sys.platform == "win32":
        return _appdata() / name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    return Path.home() / ".config" / name


# XDG configuration directory.
def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def known_clients() -> list[dict]:
    home = Path.home()
    return [
        {
            "key": "claude-code",
            "label": "Claude Code (CLI)",
            "style": "mcpServers",
            "config": home / ".claude.json",
            "detect": [home / ".claude"],
            "exe": "claude",
        },
        {
            "key": "claude-desktop",
            "label": "Claude Desktop",
            "style": "mcpServers",
            "config": _app_support("Claude") / "claude_desktop_config.json",
            "detect": [_app_support("Claude")],
            "exe": None,
        },
        {
            "key": "cursor",
            "label": "Cursor",
            "style": "mcpServers",
            "config": home / ".cursor" / "mcp.json",
            "detect": [home / ".cursor"],
            "exe": "cursor",
        },
        {
            "key": "windsurf",
            "label": "Windsurf",
            "style": "mcpServers",
            "config": home / ".codeium" / "windsurf" / "mcp_config.json",
            "detect": [home / ".codeium" / "windsurf"],
            "exe": None,
        },
        {
            # Antigravity config file mapping.
            "key": "antigravity",
            "label": "Antigravity",
            "style": "mcpServers",
            "config": home / ".gemini" / "config" / "mcp_config.json",
            "detect": [home / ".gemini" / "config"],
            "exe": "agy",
        },
        {
            "key": "vscode",
            "label": "VS Code (GitHub Copilot)",
            "style": "vscode",
            "config": _app_support("Code") / "User" / "mcp.json",
            "detect": [_app_support("Code") / "User"],
            "exe": None,
        },
        {
            "key": "codex",
            "label": "Codex (CLI / Desktop)",
            "style": "codex-toml",
            "config": home / ".codex" / "config.toml",
            "detect": [home / ".codex"],
            "exe": "codex",
        },
        {
            "key": "gemini-cli",
            "label": "Gemini CLI",
            "style": "mcpServers",
            "config": home / ".gemini" / "settings.json",
            "detect": [home / ".gemini"],
            "exe": "gemini",
        },
        {
            "key": "opencode",
            "label": "opencode",
            "style": "opencode",
            "config": _xdg_config() / "opencode" / "opencode.json",
            "detect": [_xdg_config() / "opencode"],
            "exe": "opencode",
        },
        {
            # GitHub Copilot CLI config mapping.
            "key": "copilot-cli",
            "label": "GitHub Copilot CLI",
            "style": "copilot-cli",
            "config": home / ".copilot" / "mcp-config.json",
            "detect": [home / ".copilot"],
            "exe": "copilot",
        },
    ]


def is_detected(client: dict) -> bool:
    if any(p.exists() for p in client["detect"]):
        return True
    return bool(client["exe"] and shutil.which(client["exe"]))


# Prefer absolute path so GUIs can locate the server.
def server_command() -> tuple[str, list[str]]:
    amx_exe = shutil.which("amx")
    if amx_exe:
        return amx_exe, ["server"]
    return sys.executable, ["-m", "amx.cli", "server"]


# Get client trust settings structure.
def _trust_descriptor(client: dict) -> dict:
    key = client["key"]
    if key == "claude-code":
        return {"kind": "claude-settings", "path": Path.home() / ".claude" / "settings.json"}
    if key in ("gemini-cli", "antigravity"):
        return {"kind": "server-flag", "flag": "trust"}
    hints = {
        "claude-desktop": "Claude Desktop: approve amx's tools once when first prompted.",
        "cursor": "Cursor: enable auto-run / 'always allow' for the amx server in Settings -> MCP.",
        "windsurf": "Windsurf: turn on auto-approve for the amx server in the MCP panel.",
        "vscode": "VS Code: trust the amx MCP server when first prompted.",
        "codex": 'Codex: set approval_policy in ~/.codex/config.toml (e.g. "on-failure") or trust this project.',
        "opencode": "opencode: allow the amx tools in your opencode permission settings.",
        "copilot-cli": 'Copilot CLI: run with --allow-all-tools, or approve amx\'s tools once when prompted.',
    }
    return {"kind": "ui",
            "hint": hints.get(key, "Enable 'always allow' for the amx server in the client's settings.")}


# Get client auto-approval trust status.
def trust_status(client: dict) -> Optional[bool]:
    d = _trust_descriptor(client)
    if d["kind"] == "claude-settings":
        allow = (_load_json(d["path"]).get("permissions") or {}).get("allow") or []
        return "mcp__amx" in allow
    if d["kind"] == "server-flag":
        entry = (_load_json(client["config"]).get("mcpServers") or {}).get("amx") or {}
        return bool(entry.get(d["flag"]))
    return None


# Set client auto-approval trust status.
def set_trust(client: dict, enabled: bool) -> Optional[Path]:
    d = _trust_descriptor(client)
    if d["kind"] == "claude-settings":
        path = d["path"]
        config = _load_json(path)
        allow = config.setdefault("permissions", {}).setdefault("allow", [])
        if enabled and "mcp__amx" not in allow:
            allow.append("mcp__amx")
        if not enabled and "mcp__amx" in allow:
            allow.remove("mcp__amx")
    elif d["kind"] == "server-flag":
        path = client["config"]
        config = _load_json(path)
        entry = config.setdefault("mcpServers", {}).setdefault("amx", {})
        if enabled:
            entry[d["flag"]] = True
        else:
            entry.pop(d["flag"], None)
    else:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def trust_hint(client: dict) -> str:
    return _trust_descriptor(client).get("hint", "")


# Load a JSON file or return empty dict if missing.
def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError(f"{path} exists but is not valid JSON; fix or remove it first.")


# Write AMX server entry into JSON config.
def _write_json(path: Path, top_key: str, entry: dict) -> None:
    config = _load_json(path)
    servers = config.setdefault(top_key, {})
    servers["amx"] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _write_opencode(path: Path, command: str, args: list[str]) -> None:
    config = _load_json(path)
    config.setdefault("$schema", "https://opencode.ai/config.json")
    servers = config.setdefault("mcp", {})
    servers["amx"] = {"type": "local", "command": [command, *args], "enabled": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


# Check if config table represents amx.
def _is_amx_table(stripped: str) -> bool:
    return stripped == "[mcp_servers.amx]" or stripped.startswith("[mcp_servers.amx.")


def _strip_codex_amx(lines: list[str]) -> tuple[list[str], bool]:
    kept: list[str] = []
    skipping = found = False
    for line in lines:
        stripped = line.strip()
        if _is_amx_table(stripped):
            skipping = found = True
            continue
        if skipping and stripped.startswith("["):
            skipping = False
        if not skipping:
            kept.append(line)
    return kept, found


def _write_codex_toml(path: Path, command: str, args: list[str]) -> None:
    section = (
        "[mcp_servers.amx]\n"
        f'command = "{command}"\n'
        f"args = {json.dumps(args)}\n"
    )
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept, _ = _strip_codex_amx(lines)
    text = "\n".join(kept).rstrip()
    text = (text + "\n\n" if text else "") + section
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _remove_codex_amx(path: Path) -> bool:
    if not path.is_file():
        return False
    kept, found = _strip_codex_amx(path.read_text(encoding="utf-8").splitlines())
    if found:
        text = "\n".join(kept).rstrip()
        path.write_text(text + "\n" if text else "", encoding="utf-8")
    return found


def install(client: dict) -> Path:
    command, args = server_command()
    if client["style"] == "vscode":
        _write_json(client["config"], "servers",
                    {"type": "stdio", "command": command, "args": args})
    elif client["style"] == "codex-toml":
        _write_codex_toml(client["config"], command, args)
    elif client["style"] == "opencode":
        _write_opencode(client["config"], command, args)
    elif client["style"] == "copilot-cli":
        _write_json(client["config"], "mcpServers",
                    {"type": "stdio", "command": command, "args": args})
    else:
        _write_json(client["config"], "mcpServers", {"command": command, "args": args})
    return client["config"]


_SERVER_KEY = {"vscode": "servers", "opencode": "mcp"}


# Remove AMX server configuration from client.
def uninstall(client: dict) -> Optional[Path]:
    path = client["config"]
    removed = False
    if client["style"] == "codex-toml":
        removed = _remove_codex_amx(path)
    elif path.is_file():
        top = _SERVER_KEY.get(client["style"], "mcpServers")
        config = _load_json(path)
        servers = config.get(top)
        if isinstance(servers, dict) and "amx" in servers:
            del servers["amx"]
            path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            removed = True
    # For claude-settings clients the trust grant lives in a separate file; clear it too.
    if _trust_descriptor(client)["kind"] == "claude-settings":
        set_trust(client, False)
    return path if removed else None
