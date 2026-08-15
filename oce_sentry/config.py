"""Configuration, resolved once at startup and carrying its own provenance.

Sentry owns its incident scope policy and ships one. It does not require a
checkout of the MeTA fleet, the fleet's daemon, or anything the fleet produces:
the queue is a live IcM query, and everything needed to shape that query lives
in this package.

Provenance still travels with the config, because "which policy am I running"
remains the question that decides whether the queue can be trusted. The
effective source and its content hash are shown in the status line and in
`--once`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BUNDLED_POLICY = Path(__file__).parent / "policy" / "scope.json"


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


def _env_path(name: str) -> Path | None:
    raw = _env(name)
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class Policy:
    """Incident scope policy: which IcM incidents this console is responsible for."""

    path: Path
    sha256: str
    raw: dict
    origin: str  # "bundled" | "file" | "fleet"

    @property
    def icm(self) -> dict:
        return self.raw["sources"]["icm"]

    @property
    def scope(self) -> dict:
        return self.raw["scope"]

    @property
    def short_hash(self) -> str:
        return self.sha256[:12]

    @property
    def label(self) -> str:
        return f"{self.origin}@{self.short_hash}"

    @property
    def seeded_from(self) -> str:
        meta = self.raw.get("metadata", {})
        commit = meta.get("derivedFromCommit", "")
        source = meta.get("derivedFrom", "")
        if source and commit:
            return f"{source}@{commit} on {meta.get('derivedAt', 'unknown date')}"
        return ""

    @classmethod
    def load(cls, path: Path, origin: str) -> "Policy":
        if not path.is_file():
            raise ConfigError(f"Scope policy not found at {path}.")
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

        return cls(path=path, sha256=hashlib.sha256(blob).hexdigest(), raw=raw, origin=origin)


def resolve_policy() -> Policy:
    """Explicit file, then a fleet checkout if configured, then the bundled copy.

    An explicitly configured policy that cannot be read is fatal: the operator
    asked for a specific definition of scope, and silently substituting a
    different one would be the worst available outcome. The bundled policy is
    not a fallback in that sense -- it is what this console ships with and owns.
    """
    explicit = _env_path("OCE_SENTRY_POLICY")
    if explicit:
        return Policy.load(explicit, origin="file")

    fleet = _env_path("OCE_SENTRY_FLEET_REPO")
    if fleet:
        candidate = fleet / "data-paths.json"
        if not candidate.is_file():
            raise ConfigError(
                f"OCE_SENTRY_FLEET_REPO is set to {fleet} but there is no data-paths.json there.\n"
                "Unset it to use the policy this console ships with."
            )
        return Policy.load(candidate, origin="fleet")

    return Policy.load(BUNDLED_POLICY, origin="bundled")


@dataclass(frozen=True)
class Config:
    policy: Policy
    state_dir: Path
    output_dir: Path
    kits_dir: Path | None
    watchlist_path: Path | None
    lookback_days: int
    query_timeout: int
    action_timeout: int
    intervals: dict[str, int] = field(default_factory=dict)


def _default_state_dir() -> Path:
    base = _env("LOCALAPPDATA") or _env("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "oce-sentry"


#: Where kits usually are, when nobody has said otherwise. Sentry's companion
#: is the MeTA fleet repository, and requiring an environment variable to find
#: a checkout sitting next to this one turns a working install into an empty
#: one for no reason.
_KIT_CANDIDATES = (
    Path("meta-livesite-agent-expander") / "kits",
    Path("..") / "meta-livesite-agent-expander" / "kits",
)


def _discover_kits_nearby() -> Path | None:
    roots = [Path.cwd(), Path.home() / "repos", Path.home()]
    for root in roots:
        for candidate in _KIT_CANDIDATES:
            path = (root / candidate).resolve()
            if path.is_dir():
                return path
    return None


def _resolve_kits() -> Path | None:
    """Runbooks are optional and never vendored.

    A kit source is configuration. With none configured the queue still works;
    there are simply no actions to run, which the UI states plainly rather than
    implying none exist.
    """
    explicit = _env_path("OCE_SENTRY_KITS")
    if explicit:
        return explicit if explicit.is_dir() else None

    fleet = _env_path("OCE_SENTRY_FLEET_REPO")
    if fleet:
        candidate = fleet / "kits"
        if candidate.is_dir():
            return candidate

    return _discover_kits_nearby()


def _resolve_watchlist() -> Path | None:
    """Optional enrichment: the fleet's own tracking history.

    Adds "the fleet has looked at this 25 times" to a row when it happens to be
    reachable. Nothing depends on it.
    """
    explicit = _env_path("OCE_SENTRY_WATCHLIST")
    if explicit:
        return explicit if explicit.is_file() else None

    fleet = _env_path("OCE_SENTRY_FLEET_REPO")
    if fleet:
        candidate = fleet / "watchlist-state" / "watchlist.json"
        return candidate if candidate.is_file() else None
    return None


def load_config() -> Config:
    policy = resolve_policy()
    state_dir = Path(_env("OCE_SENTRY_STATE_DIR") or _default_state_dir()).expanduser()
    output_dir = _env_path("OCE_SENTRY_OUTPUT_DIR") or state_dir / "output"

    # Writing incident query results into a source tree is how they end up
    # committed. Refuse any output directory inside a git repository.
    for parent in [output_dir, *output_dir.parents]:
        if (parent / ".git").exists():
            raise ConfigError(
                f"OCE_SENTRY_OUTPUT_DIR ({output_dir}) is inside the git repository at {parent}. "
                "Incident results must not be written into a source tree."
            )

    return Config(
        policy=policy,
        state_dir=state_dir,
        output_dir=output_dir,
        kits_dir=_resolve_kits(),
        watchlist_path=_resolve_watchlist(),
        # The window for the live query. 30 days matches how the fleet's
        # collector scopes the same question; scope.lookbackDays (90) scopes
        # history, which is a different question.
        lookback_days=_env_int("OCE_SENTRY_LOOKBACK_DAYS", 30),
        query_timeout=_env_int("OCE_SENTRY_QUERY_TIMEOUT", 120),
        action_timeout=_env_int("OCE_SENTRY_ACTION_TIMEOUT", 900),
        intervals={"incidents": _env_int("OCE_SENTRY_INCIDENTS_INTERVAL", 300)},
    )
