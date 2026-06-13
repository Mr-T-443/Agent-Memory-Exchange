"""Tests for AI client detection and configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amx import clients


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    # Isolate home directory and executables for testing.
    monkeypatch.setattr(clients.shutil, "which", lambda _name: None)
    return tmp_path


def _client(key: str) -> dict:
    return next(c for c in clients.known_clients() if c["key"] == key)


def test_known_clients_cover_popular_tools(home):
    keys = {c["key"] for c in clients.known_clients()}
    assert {"claude-code", "claude-desktop", "cursor", "windsurf", "antigravity",
            "vscode", "codex", "gemini-cli", "opencode", "copilot-cli"} <= keys


def test_copilot_cli_detected_by_exe_on_path(home, monkeypatch):
    # Verify executable presence is sufficient.
    monkeypatch.setattr(clients.shutil, "which",
                        lambda name: "/usr/bin/copilot" if name == "copilot" else None)
    assert clients.is_detected(_client("copilot-cli"))


def test_install_copilot_cli_writes_stdio_mcp_servers(home):
    path = clients.install(_client("copilot-cli"))
    assert path == home / ".copilot" / "mcp-config.json"

    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["amx"]
    assert entry["type"] == "stdio"
    assert entry["args"][-1] == "server"


def test_uninstall_copilot_cli_removes_only_amx(home):
    copilot = _client("copilot-cli")
    copilot["config"].parent.mkdir(parents=True)
    copilot["config"].write_text(json.dumps({"mcpServers": {
        "other": {"command": "keep"}, "amx": {"type": "stdio", "command": "x"},
    }}), encoding="utf-8")

    clients.uninstall(copilot)

    servers = json.loads(copilot["config"].read_text())["mcpServers"]
    assert "amx" not in servers and servers["other"]["command"] == "keep"


def test_antigravity_uses_gemini_config_mcp_file(home):
    # Verify Antigravity config path mapping.
    antigravity = _client("antigravity")
    assert antigravity["config"] == home / ".gemini" / "config" / "mcp_config.json"


def test_install_handles_empty_config_file(home):
    # Verify empty config doesn't crash installer.
    antigravity = _client("antigravity")
    antigravity["config"].parent.mkdir(parents=True)
    antigravity["config"].write_text("", encoding="utf-8")

    clients.install(antigravity)

    config = json.loads(antigravity["config"].read_text(encoding="utf-8"))
    assert config["mcpServers"]["amx"]["args"][-1] == "server"


def test_install_opencode_uses_mcp_local_schema(home):
    path = clients.install(_client("opencode"))

    config = json.loads(path.read_text(encoding="utf-8"))
    entry = config["mcp"]["amx"]
    assert entry["type"] == "local"
    assert entry["enabled"] is True
    assert entry["command"][-1] == "server"
    assert isinstance(entry["command"], list)


def test_detection_by_config_dir(home):
    assert not clients.is_detected(_client("cursor"))
    (home / ".cursor").mkdir()
    assert clients.is_detected(_client("cursor"))


def test_install_writes_mcp_servers_json(home):
    (home / ".cursor").mkdir()
    path = clients.install(_client("cursor"))

    config = json.loads(path.read_text(encoding="utf-8"))
    entry = config["mcpServers"]["amx"]
    assert entry["command"]
    assert "server" in " ".join(entry["args"]) or entry["args"] == ["server"]


def test_install_preserves_other_servers(home):
    cursor = _client("cursor")
    cursor["config"].parent.mkdir(parents=True)
    cursor["config"].write_text(
        json.dumps({"mcpServers": {"other": {"command": "keep-me"}}}), encoding="utf-8"
    )

    clients.install(cursor)

    config = json.loads(cursor["config"].read_text(encoding="utf-8"))
    assert config["mcpServers"]["other"]["command"] == "keep-me"
    assert "amx" in config["mcpServers"]


def test_install_rejects_corrupt_json(home):
    cursor = _client("cursor")
    cursor["config"].parent.mkdir(parents=True)
    cursor["config"].write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        clients.install(cursor)


def test_install_vscode_uses_servers_key_with_stdio(home):
    path = clients.install(_client("vscode"))

    config = json.loads(path.read_text(encoding="utf-8"))
    entry = config["servers"]["amx"]
    assert entry["type"] == "stdio"


def test_install_codex_appends_toml_section(home):
    codex = _client("codex")
    codex["config"].parent.mkdir(parents=True)
    codex["config"].write_text('[model]\nname = "gpt"\n', encoding="utf-8")

    clients.install(codex)

    text = codex["config"].read_text(encoding="utf-8")
    assert "[model]" in text
    assert "[mcp_servers.amx]" in text
    assert text.count("[mcp_servers.amx]") == 1


def test_install_codex_replaces_existing_section(home):
    codex = _client("codex")
    codex["config"].parent.mkdir(parents=True)
    codex["config"].write_text(
        '[mcp_servers.amx]\ncommand = "stale"\nargs = []\n\n[other]\nkey = 1\n',
        encoding="utf-8",
    )

    clients.install(codex)

    text = codex["config"].read_text(encoding="utf-8")
    assert "stale" not in text
    assert text.count("[mcp_servers.amx]") == 1
    assert "[other]" in text


def test_uninstall_codex_removes_amx_subtables(home):
    # Verify Codex sub-tables are removed on uninstall.
    codex = _client("codex")
    codex["config"].parent.mkdir(parents=True)
    codex["config"].write_text(
        '[model]\nname = "gpt"\n\n'
        '[mcp_servers.amx]\ncommand = "amx"\nargs = ["server"]\n\n'
        '[mcp_servers.amx.env]\nFOO = "bar"\n\n'
        '[mcp_servers.other]\ncommand = "keep"\n',
        encoding="utf-8",
    )

    clients.uninstall(codex)

    text = codex["config"].read_text(encoding="utf-8")
    assert "mcp_servers.amx" not in text          # neither the table nor any sub-table
    assert "[mcp_servers.other]" in text          # other server untouched
    assert "[model]" in text


def test_codex_install_launch_uninstall_relaunch_is_clean(home):
    # Verify full install-uninstall flow for Codex.
    codex = _client("codex")
    codex["config"].parent.mkdir(parents=True)
    codex["config"].write_text('[model]\nname = "gpt"\n', encoding="utf-8")

    clients.install(codex)
    # Simulate Codex normalizing the file and appending a sub-table on first launch.
    with codex["config"].open("a", encoding="utf-8") as f:
        f.write('\n[mcp_servers.amx.env]\nTOKEN = "x"\n')

    clients.uninstall(codex)

    text = codex["config"].read_text(encoding="utf-8")
    assert "mcp_servers.amx" not in text
    assert "[model]" in text


@pytest.mark.parametrize("key", [
    "cursor", "vscode", "codex", "opencode", "gemini-cli", "copilot-cli",
])
def test_install_then_uninstall_is_fully_reversible(home, key):
    # Verify install-uninstall is reversible for all styles.
    client = _client(key)
    client["config"].parent.mkdir(parents=True, exist_ok=True)
    original = '[other]\nkey = 1\n' if client["style"] == "codex-toml" else \
        json.dumps({"keep": True}, indent=2) + "\n"
    client["config"].write_text(original, encoding="utf-8")

    clients.install(client)
    clients.uninstall(client)

    assert "amx" not in client["config"].read_text(encoding="utf-8")


def test_cli_install_mcp_list_and_client_flag(home, capsys):
    from amx.cli import main

    (home / ".cursor").mkdir()
    assert main(["install-mcp", "--list"]) == 0
    out = capsys.readouterr().out
    assert "cursor" in out and "detected" in out

    assert main(["install-mcp", "--client", "cursor"]) == 0
    assert (home / ".cursor" / "mcp.json").is_file()


def test_cli_install_mcp_rejects_unknown_client(home, capsys):
    from amx.cli import main

    assert main(["install-mcp", "--client", "notepad"]) == 1
    assert "Unknown client" in capsys.readouterr().out


def test_trust_claude_code_writes_settings_allow(home):
    cc = _client("claude-code")
    assert clients.trust_status(cc) is False  # nothing set yet

    path = clients.set_trust(cc, True)
    assert path == home / ".claude" / "settings.json"
    allow = json.loads(path.read_text())["permissions"]["allow"]
    assert "mcp__amx" in allow
    assert clients.trust_status(cc) is True

    # Verify disabling trust clears allowance.
    clients.set_trust(cc, False)
    assert clients.trust_status(cc) is False


def test_trust_server_flag_marks_gemini_entry(home):
    gemini = _client("gemini-cli")
    clients.install(gemini)            # write the server entry first
    clients.set_trust(gemini, True)

    entry = json.loads(gemini["config"].read_text())["mcpServers"]["amx"]
    assert entry["trust"] is True
    assert entry["command"]  # Verify server commands are preserved.
    assert clients.trust_status(gemini) is True


def test_trust_ui_client_is_not_file_writable(home):
    cursor = _client("cursor")
    assert clients.trust_status(cursor) is None     # UI-managed
    assert clients.set_trust(cursor, True) is None  # UI-managed
    assert clients.trust_hint(cursor)  # Verify hints are available.


def test_cli_install_mcp_trust_flag(home):
    from amx.cli import main

    (home / ".claude").mkdir()
    assert main(["install-mcp", "--client", "claude-code", "--trust"]) == 0
    allow = json.loads((home / ".claude" / "settings.json").read_text())["permissions"]["allow"]
    assert "mcp__amx" in allow


def test_uninstall_removes_only_amx_entry(home):
    cursor = _client("cursor")
    cursor["config"].parent.mkdir(parents=True)
    cursor["config"].write_text(json.dumps({"mcpServers": {
        "other": {"command": "keep-me"}, "amx": {"command": "x"},
    }}), encoding="utf-8")

    path = clients.uninstall(cursor)

    config = json.loads(path.read_text(encoding="utf-8"))
    assert "amx" not in config["mcpServers"]
    assert config["mcpServers"]["other"]["command"] == "keep-me"


def test_uninstall_returns_none_when_no_amx_entry(home):
    cursor = _client("cursor")
    cursor["config"].parent.mkdir(parents=True)
    cursor["config"].write_text(json.dumps({"mcpServers": {"other": {}}}), encoding="utf-8")
    assert clients.uninstall(cursor) is None
    # Verify other servers are untouched.
    assert "other" in json.loads(cursor["config"].read_text())["mcpServers"]


def test_uninstall_removes_only_codex_section(home):
    codex = _client("codex")
    codex["config"].parent.mkdir(parents=True)
    codex["config"].write_text(
        '[model]\nname = "gpt"\n\n[mcp_servers.amx]\ncommand = "amx"\nargs = []\n',
        encoding="utf-8",
    )

    clients.uninstall(codex)

    text = codex["config"].read_text(encoding="utf-8")
    assert "[model]" in text
    assert "[mcp_servers.amx]" not in text


def test_uninstall_clears_claude_settings_trust(home):
    cc = _client("claude-code")
    # Mock server and trust settings.
    cc["config"].write_text(json.dumps({"mcpServers": {"amx": {"command": "x"}}}), encoding="utf-8")
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"allow": ["mcp__amx", "Bash(ls)"]}}), encoding="utf-8")

    clients.uninstall(cc)

    assert "amx" not in json.loads(cc["config"].read_text())["mcpServers"]
    allow = json.loads(settings.read_text())["permissions"]["allow"]
    assert "mcp__amx" not in allow and "Bash(ls)" in allow


def test_cli_install_mcp_remove_flag(home):
    from amx.cli import main

    (home / ".cursor").mkdir()
    clients.install(_client("cursor"))
    assert main(["install-mcp", "--client", "cursor", "--remove"]) == 0
    assert "amx" not in json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
