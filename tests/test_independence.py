"""Independence tests.

Sentry ships its own scope policy and must start with no configuration at all.
It should be possible to `pip install` it on a machine that has never heard of
the goobers pipeline and get a working queue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oce_sentry.config import BUNDLED_POLICY, ConfigError, load_config, resolve_policy

ENV_VARS = [
    "OCE_SENTRY_FLEET_REPO",
    "OCE_SENTRY_POLICY",
    "OCE_SENTRY_KITS",
    "OCE_SENTRY_WATCHLIST",
    "OCE_SENTRY_OUTPUT_DIR",
    "OCE_SENTRY_STATE_DIR",
]


@pytest.fixture
def bare(monkeypatch, tmp_path):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OCE_SENTRY_STATE_DIR", str(tmp_path / "state"))
    # Isolate from the developer's own checkouts: kit auto-discovery looks at
    # the working directory and the home directory, so a real repository next
    # door would otherwise decide the result of these tests.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_starts_with_no_configuration_at_all(bare):
    config = load_config()
    assert config.policy.origin == "bundled"
    assert config.kits_dir is None
    assert config.watchlist_path is None


def test_bundled_policy_is_shipped_and_complete():
    assert BUNDLED_POLICY.is_file(), "the scope policy must ship inside the package"
    raw = json.loads(BUNDLED_POLICY.read_text(encoding="utf-8"))
    icm = raw["sources"]["icm"]
    assert icm["cluster"].startswith("https://")
    assert icm["database"]
    assert icm["tables"]["incidents"]
    scope = raw["scope"]
    assert scope["teams"], "policy must name the owning teams"
    assert scope["autoMitigation"]["identities"]
    assert scope["environments"]["classification"]


def test_bundled_policy_records_where_it_came_from(bare):
    """Provenance is the substitute for the hard dependency.

    Sentry no longer reads the fleet's file, so the only way to tell whether its
    copy has fallen behind is to record what it was seeded from.
    """
    policy = resolve_policy()
    assert policy.seeded_from, "the bundled policy must record its origin"
    assert "data-paths.json" in policy.seeded_from
    assert policy.label.startswith("bundled@")


def test_a_fleet_checkout_is_used_when_offered(bare, monkeypatch):
    fleet = bare / "fleet"
    fleet.mkdir()
    (fleet / "data-paths.json").write_text(
        BUNDLED_POLICY.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("OCE_SENTRY_FLEET_REPO", str(fleet))
    assert resolve_policy().origin == "fleet"


def test_a_fleet_checkout_without_a_policy_is_an_error(bare, monkeypatch):
    """Better than silently using the bundled copy.

    The operator pointed at a checkout expecting to track its definition; not
    finding one is a mistake worth naming.
    """
    empty = bare / "empty"
    empty.mkdir()
    monkeypatch.setenv("OCE_SENTRY_FLEET_REPO", str(empty))
    with pytest.raises(ConfigError, match="no data-paths.json"):
        resolve_policy()


def test_kits_can_be_configured_without_a_fleet_checkout(bare, monkeypatch):
    kits = bare / "runbooks"
    kits.mkdir()
    monkeypatch.setenv("OCE_SENTRY_KITS", str(kits))
    assert load_config().kits_dir == kits


def test_no_kits_configured_is_not_an_error(bare):
    # The queue is the point; runbooks are additive.
    assert load_config().kits_dir is None


