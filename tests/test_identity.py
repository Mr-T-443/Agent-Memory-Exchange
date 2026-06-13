import subprocess

import amx.identity.project_id as project_id_mod
from amx.identity import normalize_git_remote, resolve_project_id


def test_explicit_project_id_wins():
    pid, _, _ = resolve_project_id(project_id="abc", project_name="ignored", cwd="C:/x")
    assert pid == "abc"


def test_name_beats_cwd():
    pid, _, _ = resolve_project_id(project_name="My Cool Project", cwd="C:/x")
    assert pid == "name-my-cool-project"


def test_path_hash_is_stable(tmp_path):
    a, _, _ = resolve_project_id(cwd=str(tmp_path))
    b, _, _ = resolve_project_id(cwd=str(tmp_path))
    assert a == b
    assert a.startswith(("path-", "git-"))


def test_no_hints_falls_back_to_default():
    pid, _, _ = resolve_project_id()
    assert pid == "default"


def test_git_subprocess_detaches_stdin(monkeypatch, tmp_path):
    """Verify git subprocess detaches stdin to prevent hangs on stdio transport."""
    seen = {}
    real_popen = subprocess.Popen

    def spying_popen(args, **kwargs):
        seen["stdin"] = kwargs.get("stdin")
        return real_popen(args, **kwargs)

    monkeypatch.setattr(project_id_mod.subprocess, "Popen", spying_popen)
    project_id_mod._git_remote(str(tmp_path))
    assert seen["stdin"] == subprocess.DEVNULL


def test_git_remote_normalization_converges_variants():
    """Verify various git remote URL formats normalize to a canonical format."""
    canonical = "github.com/org/repo"
    variants = [
        "git@github.com:org/repo.git",
        "https://github.com/org/repo.git",
        "https://github.com/org/repo/",
        "https://user:token@github.com/org/repo",
        "ssh://git@github.com/org/repo.git",
        "HTTPS://GitHub.com/Org/Repo.git",
    ]
    assert {normalize_git_remote(v) for v in variants} == {canonical}


def test_git_remote_normalization_keeps_distinct_hosts():
    a = normalize_git_remote("git@github.com:org/repo.git")
    b = normalize_git_remote("git@gitlab.com:org/repo.git")
    assert a != b


def test_git_remote_is_not_canonical_in_resolution(monkeypatch, tmp_path):
    """Verify git remote is not normalized during initial project ID resolution."""
    def fake_remote(url):
        return lambda cwd: url

    monkeypatch.setattr(project_id_mod, "_git_remote", fake_remote("git@github.com:org/repo.git"))
    ssh_id, _, _ = resolve_project_id(cwd=str(tmp_path))
    monkeypatch.setattr(project_id_mod, "_git_remote", fake_remote("https://github.com/org/repo.git"))
    https_id, _, _ = resolve_project_id(cwd=str(tmp_path))
    assert ssh_id != https_id
    assert ssh_id.startswith("git-") and https_id.startswith("git-")
    # Normalization matches the variants for deduplication.
    assert normalize_git_remote("git@github.com:org/repo.git") == normalize_git_remote(
        "https://github.com/org/repo.git"
    )
