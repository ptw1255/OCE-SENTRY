"""Configuration, resolved once at startup and carrying its own provenance.

Provenance matters more than it looks. The incident scope this console shows is
policy owned by the fleet, not by the console, and a console quietly running a
stale copy of that policy would show a confidently wrong queue. So the effective
policy source and its content hash travel with the config and are displayed.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Configuration could not be resolved. Always fatal, always explained."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Policy:
    """Incident scope policy, read from the fleet's data-paths.json.

    This is deliberately not defaulted. A built-in fallback would let the console
    keep running after the fleet changed its scope, showing a queue that no longer
    matches what the fleet and its reports consider in scope -- the exact class of
    silent drift this console exists to expose elsewhere.
    """

    path: Path
    sha256: str
    raw: dict

    @property
    def icm(self) -> dict:
        return self.raw["sources"]["icm"]

    @property
    def scope(self) -> dict:
        return self.raw["scope"]

    @property
    def short_hash(self) -> str:
        return self.sha256[:12]

    @classmethod
    def load(cls, path: Path) -> "Policy":
        if not path.is_file():
            raise ConfigError(
                f"Scope policy not found at {path}.\n"
                "This console reads the fleet's data-paths.json so its queue matches the "
                "fleet's. Point OCE_SENTRY_FLEET_REPO at a checkout of "
                "meta-livesite-agent-expander, or set OCE_SENTRY_POLICY to the file directly."
            )
        blob = path.read_bytes()
        try:
            raw = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Scope policy at {path} is not valid JSON: {exc}") from exc

        for required in ("sources", "scope"):
            if required not in raw:
                raise ConfigError(f"Scope policy at {path} has no {required!r} section.")
        if "icm" not in raw["sources"]:
            raise ConfigError(f"Scope policy at {path} has no sources.icm section.")

        return cls(path=path, sha256=hashlib.sha256(blob).hexdigest(), raw=raw)


@dataclass(frozen=True)
class Config:
    fleet_repo: Path | None
    policy: Policy
    state_dir: Path
    output_dir: Path
    lookback_days: int
    query_timeout: int
    action_timeout: int
    intervals: dict[str, int] = field(default_factory=dict)

    @property
    def kits_dir(self) -> Path | None:
        if self.fleet_repo is None:
            return None
        kits = self.fleet_repo / "kits"
        return kits if kits.is_dir() else None

    @property
    def watchlist_path(self) -> Path | None:
        """Optional enrichment, present only on the machine running the fleet.

        The queue does not depend on this -- it is queried live. This adds the
        fleet's tracking history (how many runs have already looked at an
        incident) when it happens to be reachable.
        """
        if self.fleet_repo is None:
            return None
        candidate = self.fleet_repo / "watchlist-state" / "watchlist.json"
        return candidate if candidate.is_file() else None


def _default_state_dir() -> Path:
    base = _env("LOCALAPPDATA") or _env("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "oce-sentry"


def load_config() -> Config:
    """Resolve configuration from the environment. Fails loudly and specifically."""
    fleet_repo_raw = _env("OCE_SENTRY_FLEET_REPO")
    fleet_repo = Path(fleet_repo_raw).expanduser() if fleet_repo_raw else None
    if fleet_repo is not None and not fleet_repo.is_dir():
        raise ConfigError(f"OCE_SENTRY_FLEET_REPO is not a directory: {fleet_repo}")

    policy_raw = _env("OCE_SENTRY_POLICY")
    if policy_raw:
        policy_path = Path(policy_raw).expanduser()
    elif fleet_repo is not None:
        policy_path = fleet_repo / "data-paths.json"
    else:
        raise ConfigError(
            "No scope policy configured.\n"
            "Set OCE_SENTRY_FLEET_REPO to a checkout of meta-livesite-agent-expander, "
            "or OCE_SENTRY_POLICY to a data-paths.json."
        )

    state_dir = Path(_env("OCE_SENTRY_STATE_DIR") or _default_state_dir()).expanduser()

    output_raw = _env("OCE_SENTRY_OUTPUT_DIR")
    output_dir = Path(output_raw).expanduser() if output_raw else state_dir / "output"

    # Writing incident query results into a source tree is how they end up
    # committed. The fleet's own kits already do this (tracked upstream); the
    # console will not add to it.
    if fleet_repo is not None:
        try:
            output_dir.resolve().relative_to(fleet_repo.resolve())
        except (ValueError, OSError):
            pass
        else:
            raise ConfigError(
                f"OCE_SENTRY_OUTPUT_DIR ({output_dir}) is inside the fleet repository. "
                "Incident results must not be written into a source tree."
            )

    return Config(
        fleet_repo=fleet_repo,
        policy=Policy.load(policy_path),
        state_dir=state_dir,
        output_dir=output_dir,
        # The fleet's watchlist collector defaults to 30 days, not the 90 in
        # data-paths.lookbackDays (which scopes history, not the live queue).
        # Matching the collector keeps the two queues comparable.
        lookback_days=_env_int("OCE_SENTRY_LOOKBACK_DAYS", 30),
        query_timeout=_env_int("OCE_SENTRY_QUERY_TIMEOUT", 120),
        action_timeout=_env_int("OCE_SENTRY_ACTION_TIMEOUT", 900),
        intervals={
            "incidents": _env_int("OCE_SENTRY_INCIDENTS_INTERVAL", 300),
        },
    )
