"""Tests for user profile storage and retrieval in context bundles."""

from __future__ import annotations

from amx.memory.bundle import build_bundle
from amx.state.project_state import update_project_state


def test_profile_roundtrip(store):
    store.set_profile("Embedded dev; prefers Rust; building AMX.")
    profile = store.get_profile()
    assert profile["text"] == "Embedded dev; prefers Rust; building AMX."
    assert profile["updated_at"] is not None


def test_profile_replace_is_full(store):
    store.set_profile("Old profile.")
    store.set_profile("New profile.")
    assert store.get_profile()["text"] == "New profile."


def test_profile_cold_install_is_none(store):
    assert store.get_profile() is None


def test_profile_clear(store):
    store.set_profile("Something.")
    store.clear_profile()
    assert store.get_profile() is None


def test_bundle_leads_with_profile(store, cfg):
    store.set_profile("Prefers Rust.")
    update_project_state(store, "p1", {"current_goal": "Build AMX"})
    bundle = build_bundle(store, "p1", cfg)
    assert bundle.slices[0].kind == "user_profile"
    assert bundle.slices[0].content == "Prefers Rust."
    assert bundle.slices[1].kind == "project_state"


def test_cold_start_bundle_includes_profile(store, cfg):
    store.set_profile("Prefers Rust.")
    bundle = build_bundle(store, "fresh-project", cfg)
    assert bundle.cold_start is True
    assert bundle.slices[0].kind == "user_profile"
    assert bundle.slices[1].kind == "project_state"


def test_bundle_without_profile_unchanged(store, cfg):
    update_project_state(store, "p1", {"current_goal": "Build AMX"})
    bundle = build_bundle(store, "p1", cfg)
    assert all(s.kind != "user_profile" for s in bundle.slices)
    assert bundle.slices[0].kind == "project_state"
