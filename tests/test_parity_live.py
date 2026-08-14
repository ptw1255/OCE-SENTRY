"""Live parity check against the fleet's own watchlist.

Opt-in: needs `az login`, network access and a fleet checkout whose daemon has
run. Set OCE_SENTRY_LIVE=1 to enable.

This is the test that matters most and the one that cannot run in isolation.
Counting rows is not enough -- a different set of the same size is not parity --
so it compares exact incident ids and track reasons. The console builds its KQL
from the same data-paths.json the fleet does, so any drift here means the
translation has diverged, and a diverged queue is silently wrong rather than
loudly broken.
"""

from __future__ import annotations

import json
import os

import pytest

from oce_sentry.auth import TokenProvider
from oce_sentry.config import load_config
from oce_sentry.kusto import KustoClient
from oce_sentry.sources.incidents import fetch_incidents

pytestmark = pytest.mark.skipif(
    os.environ.get("OCE_SENTRY_LIVE") != "1",
    reason="live test; set OCE_SENTRY_LIVE=1 with az login and a fleet checkout",
)


@pytest.fixture(scope="module")
def live():
    config = load_config()
    if config.watchlist_path is None:
        pytest.skip("no watchlist state; this machine is not running the fleet")
    result = fetch_incidents(config, KustoClient(TokenProvider(), timeout=config.query_timeout))
    if not result.ok:
        pytest.fail(f"incidents unavailable: {result.error}")
    watchlist = json.loads(config.watchlist_path.read_text(encoding="utf-8-sig"))
    return result, watchlist


def test_incident_ids_match_the_fleet_exactly(live):
    result, watchlist = live
    ours = {i.incident_id for i in result.data}
    theirs = {str(e["incidentId"]) for e in watchlist.get("active", [])}

    only_ours = sorted(ours - theirs)
    only_theirs = sorted(theirs - ours)

    # A small symmetric difference is legitimate: the fleet's snapshot is up to
    # 20 minutes old, so an incident can open or close in between. A one-sided
    # or large difference means the scope translation has drifted.
    assert len(only_ours) + len(only_theirs) <= 2, (
        f"queue diverged from the fleet.\n"
        f"  only in console: {only_ours}\n"
        f"  only in fleet:   {only_theirs}"
    )


def test_track_reasons_match_for_shared_incidents(live):
    result, watchlist = live
    theirs = {str(e["incidentId"]): e for e in watchlist.get("active", [])}
    mismatches = [
        (i.incident_id, i.track_reason, theirs[i.incident_id].get("trackReason"))
        for i in result.data
        if i.incident_id in theirs and i.track_reason != theirs[i.incident_id].get("trackReason")
    ]
    assert not mismatches, f"trackReason disagreements: {mismatches}"


def test_query_is_not_pathologically_slow(live):
    result, _ = live
    assert result.detail["duration_ms"] < 30000
