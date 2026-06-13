from __future__ import annotations

import os

import pytest

from amx.config import AMXConfig
from amx.store import Store


# Isolate environment by clearing AMX_* variables.
@pytest.fixture(autouse=True)
def _isolate_amx_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("AMX_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def cfg(tmp_path) -> AMXConfig:
    return AMXConfig(db_path=tmp_path / "amx.db")


@pytest.fixture
def store(cfg) -> Store:
    s = Store(cfg.db_path)
    yield s
    s.close()
