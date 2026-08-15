"""The skill browser's contents.

The browser lists skills. It used to also list the fleet's Kusto query kits,
which was a leftover from before kits and skills were separated -- and put four
generated query folders at the bottom of a list of investigation techniques.
"""

from __future__ import annotations

import pytest

from oce_sentry.catalog import build_catalog, is_maintenance
from oce_sentry.config import load_config


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def has_kits(config) -> bool:
    from oce_sentry.actions import discover_kits

    return bool(discover_kits(config))


def test_query_kits_are_not_in_the_skill_browser(config):
    """A generated query folder keyed to one monitor is not a skill."""
    assert not [e for e in build_catalog(config) if e.source == "kusto"]


def test_query_kits_are_still_reachable_when_asked_for(config, has_kits):
    if not has_kits:
        pytest.skip("no fleet kit checkout on this machine")
    entries = build_catalog(config, include_queries=True)
    assert [e for e in entries if e.source == "kusto"]


def test_the_browser_lists_skills(config):
    sources = {e.source for e in build_catalog(config)}
    assert sources <= {"skill", "link"}


def test_maintenance_skills_stay_hidden_by_default(config):
    shown = {e.id for e in build_catalog(config)}
    assert not any(is_maintenance(skill_id) for skill_id in shown)


def test_maintenance_can_be_revealed(config):
    curated = {e.id for e in build_catalog(config)}
    everything = {e.id for e in build_catalog(config, include_maintenance=True)}
    assert curated <= everything
