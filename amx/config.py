"""Runtime configuration and environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from amx.schema import RecordType


# Coefficients for ranking signals (must sum to 1.0).
@dataclass(frozen=True)
class RankingWeights:
    relevance: float = 0.50
    recency: float = 0.25
    type_weight: float = 0.15
    entity_overlap: float = 0.10


# Priority weights for record types in tie-breakers.
TYPE_WEIGHTS: dict[RecordType, float] = {
    RecordType.PROJECT_STATE: 1.0,
    RecordType.DECISION: 0.9,
    RecordType.ARCHITECTURE: 0.85,  # design-level, just below decisions
    RecordType.SUMMARY: 0.8,
    RecordType.TASK: 0.7,
    RecordType.BUG: 0.7,            # actionable, like tasks
    RecordType.THREAD: 0.6,
    RecordType.RESEARCH: 0.55,      # reference notes, above generic entities
    RecordType.ENTITY: 0.5,
    RecordType.ARTIFACT_REFERENCE: 0.5,
    RecordType.RAW_EVENT: 0.3,
}


# Load AMX_* environment variables from ~/.amx/.env file.
def _load_env_file(path: Path | None = None) -> None:
    if path is None:
        path = Path.home() / ".amx" / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key.startswith("AMX_") and key not in os.environ:
            os.environ[key] = value


_load_env_file()


# Read integer env variable, falling back to default on error.
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"AMX: ignoring invalid {name}={raw!r} (not an integer); using {default}.",
              file=sys.stderr)
        return default


# Update or write a single key-value pair to the env file.
def _set_env_file_key(key: str, value: str, path: Path | None = None) -> None:
    if path is None:
        path = Path.home() / ".amx" / ".env"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    updated = False
    result = []
    for line in lines:
        k, _, _ = line.partition("=")
        if k.strip() == key:
            result.append(f"{key}={value}")
            updated = True
        else:
            result.append(line)
    if not updated:
        result.append(f"{key}={value}")
    path.write_text("\n".join(result) + "\n", encoding="utf-8")


# Get database file path from environment or default to ~/.amx/amx.db.
def _default_db_path() -> Path:
    env = os.environ.get("AMX_DB_PATH")
    if env:
        return Path(env)
    return Path.home() / ".amx" / "amx.db"


# AMX configuration settings.
@dataclass
class AMXConfig:
    db_path: Path = field(default_factory=_default_db_path)
    weights: RankingWeights = field(default_factory=RankingWeights)
    recency_half_life_days: float = 7.0
    default_budget_tokens: int = 3000
    bundle_safety_margin: float = 0.10  # budgets are advisory targets
    search_limit: int = 20
    max_bundle_decisions: int = 5

    # Cross-project discovery limits.
    discovery_limit: int = 5
    discovery_score_floor: float = 0.0

    profile_max_tokens: int = field(
        default_factory=lambda: _env_int("AMX_PROFILE_MAX_TOKENS", 100)
    )

    # Cold start context limits.
    digest_budget_tokens: int = field(
        default_factory=lambda: _env_int("AMX_DIGEST_BUDGET_TOKENS", 100)
    )
    chat_summary_max_tokens: int = field(
        default_factory=lambda: _env_int("AMX_CHAT_SUMMARY_MAX_TOKENS", 30)
    )

    # Foundry IQ grounding configuration.
    foundry_endpoint: str = field(
        default_factory=lambda: os.environ.get("AMX_FOUNDRY_IQ_ENDPOINT", "")
    )
    foundry_api_key: str = field(
        default_factory=lambda: os.environ.get("AMX_FOUNDRY_IQ_API_KEY", "")
    )
    foundry_index: str = field(
        default_factory=lambda: os.environ.get("AMX_FOUNDRY_IQ_INDEX", "")
    )

    # Enable automatic background synchronization with Foundry IQ.
    foundry_sync_enabled: bool = field(
        default_factory=lambda: os.environ.get("AMX_FOUNDRY_SYNC", "").lower() == "true"
    )

    # Check if Foundry IQ is configured.
    @property
    def foundry_configured(self) -> bool:
        return bool(self.foundry_endpoint and self.foundry_api_key)
