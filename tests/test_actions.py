"""Action discovery, command construction and safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from oce_sentry.actions import Action, actions_for, build_command, discover_kits
from oce_sentry.config import Config, ConfigError, Policy, load_config
from oce_sentry.models import Incident

FIXTURE = Path(__file__).parent / "fixtures" / "scope.json"

ENV_VARS = (
    "OCE_SENTRY_FLEET_REPO",
    "OCE_SENTRY_POLICY",
    "OCE_SENTRY_KITS",
    "OCE_SENTRY_WATCHLIST",
    "OCE_SENTRY_OUTPUT_DIR",
)


def _clear_env(monkeypatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="850000001",
        title='[Failed Ping] "quoted" & (parens) | pipe',
        severity=2.0,
        severity_raw=2,
        status="ACTIVE",
        incident_type="LiveSite",
        track_reason="sev2-or-2.5-not-auto",
        monitor_id="MicroservicePing",
        owning_team_id="104519",
        owning_team_name="team",
        owning_contact_alias="alias",
        create_date="2026-01-01T00:00:00Z",
        mitigate_date=None,
        mitigated_by=None,
        is_terminal=False,
        minutes_open=60.0,
        is_customer_impacting=False,
        env_class="PROD",
        tsg_id="",
    )
    base.update(kwargs)
    return Incident(**base)


def _config(tmp_path: Path, kits_root: Path | None = None) -> Config:
    kits = kits_root / "kits" if kits_root else None
    return Config(
        policy=Policy.load(FIXTURE, origin="file"),
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        kits_dir=kits if kits and kits.is_dir() else None,
        watchlist_path=None,
        lookback_days=30,
        query_timeout=30,
        action_timeout=60,
        intervals={},
    )


def _make_kit(root: Path, slug: str, monitor: str) -> Path:
    kit = root / "kits" / slug
    kit.mkdir(parents=True)
    (kit / "run.ps1").write_text("param([string]$IncidentId)\n", encoding="utf-8")
    (kit / "monitor-entry.yaml.txt").write_text(f'  monitor_id: "{monitor}"\n', encoding="utf-8")
    (kit / "README.md").write_text("Evidence: 8 firings, 37.5% auto-mitigated\n", encoding="utf-8")
    return kit


def test_discovers_kits_and_reads_monitor_id(tmp_path):
    _make_kit(tmp_path, "ping-a", "MicroservicePing")
    kits = discover_kits(_config(tmp_path, tmp_path))
    assert [k.id for k in kits] == ["ping-a"]
    assert kits[0].monitor_id == "MicroservicePing"


def test_no_kits_configured_yields_no_actions(tmp_path):
    # Independence: a machine with no runbook source still runs.
    assert discover_kits(_config(tmp_path)) == []


def test_kit_is_not_read_only_because_it_writes_beside_itself():
    """Tracked upstream as meta-livesite-agent-expander#138.

    Declaring it keeps the confirmation honest instead of promising a read-only
    run that leaves a file behind.
    """
    action = Action(id="k", title="k", kind="kit", source="s", writes=["k/result-*.json"])
    assert not action.read_only


def test_ambiguous_monitor_returns_every_candidate(tmp_path):
    """monitorId does not uniquely identify a kit.

    MicroservicePing maps to one kit per farm signature. Choosing arbitrarily
    would run the wrong farm's query, return rows, and look authoritative.
    """
    _make_kit(tmp_path, "ping-dprodmgd151", "MicroservicePing")
    _make_kit(tmp_path, "ping-dprodmgd203", "MicroservicePing")
    kits = discover_kits(_config(tmp_path, tmp_path))
    matches = actions_for(_incident(), kits)
    assert sorted(a.id for a in matches) == ["ping-dprodmgd151", "ping-dprodmgd203"]


def test_incident_without_monitor_id_matches_nothing(tmp_path):
    _make_kit(tmp_path, "ping-a", "MicroservicePing")
    kits = discover_kits(_config(tmp_path, tmp_path))
    assert actions_for(_incident(monitor_id=""), kits) == []


def test_tsg_is_offered_as_an_action(tmp_path):
    kits = discover_kits(_config(tmp_path))
    matches = actions_for(_incident(tsg_id="https://eng.ms/tsg"), kits)
    assert [a.kind for a in matches] == ["link"]
    assert matches[0].read_only


def test_command_is_an_argv_not_a_string(tmp_path):
    """Incident-derived values stay in their own slots.

    Titles contain quotes, pipes and parentheses. Passing them through a shell
    is how data becomes syntax, and this runs as a user with production access.
    """
    kit = _make_kit(tmp_path, "ping-a", "MicroservicePing")
    action = discover_kits(_config(tmp_path, tmp_path))[0]
    command = build_command(action, _incident())

    assert isinstance(command, list)
    assert "-NoProfile" in command and "-NonInteractive" in command
    assert command[command.index("-File") + 1] == str(kit / "run.ps1")
    assert command[command.index("-IncidentId") + 1] == "850000001"
    # The title never reaches the command line at all.
    assert not any("quoted" in part for part in command)


def test_link_action_is_not_executable():
    action = Action(id="tsg", title="tsg", kind="link", source="s", url="https://example")
    with pytest.raises(ValueError):
        build_command(action, _incident())


def test_output_dir_inside_a_git_repo_is_refused(tmp_path, monkeypatch):
    """Query results must not land in a source tree.

    That is exactly how incident telemetry ends up committed.
    """
    repo = tmp_path / "somerepo"
    (repo / ".git").mkdir(parents=True)

    _clear_env(monkeypatch)
    monkeypatch.setenv("OCE_SENTRY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OCE_SENTRY_OUTPUT_DIR", str(repo / "results"))
    with pytest.raises(ConfigError, match="inside the git repository"):
        load_config()


def test_explicit_policy_that_is_missing_is_fatal(tmp_path, monkeypatch):
    """An operator who names a policy gets that policy or an error.

    Silently substituting a different definition of scope would be worse than
    not starting.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("OCE_SENTRY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OCE_SENTRY_POLICY", str(tmp_path / "nope.json"))
    with pytest.raises(ConfigError, match="Scope policy not found"):
        load_config()
