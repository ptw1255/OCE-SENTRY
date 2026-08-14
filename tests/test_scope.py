"""Scope translation tests.

These matter more than they look. The console's queue is only trustworthy while
its KQL means the same thing as the fleet's, and every failure mode here is
silent: a wrong branch order or a missing filter returns a plausible-looking set
of incidents that is quietly the wrong one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oce_sentry.sources.incidents import (
    build_environment_classifier,
    build_watchlist_query,
    sort_incidents,
)
from oce_sentry.models import Incident

FIXTURE = Path(__file__).parent / "fixtures" / "scope.json"


@pytest.fixture
def scope() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["scope"]


def test_query_filters_to_owning_teams(scope):
    query = build_watchlist_query(scope, "IncidentsSnapshotV2", 30)
    assert "OwningTeamId in (104519, 81181, 96608)" in query


def test_query_excludes_purged(scope):
    assert "| where IsPurged == false" in build_watchlist_query(scope, "IncidentsSnapshotV2", 30)


def test_severity_25_is_normalised_to_two_point_five(scope):
    # IcM encodes Sev 2.5 as the integer 25. Sorting on the raw column ranks it
    # below Sev 3, which is backwards -- 2.5 is more urgent.
    query = build_watchlist_query(scope, "IncidentsSnapshotV2", 30)
    assert "iff(Severity == 25, 2.5, todouble(Severity))" in query


def test_customer_branches_precede_severity_branches(scope):
    """Ordering is load-bearing.

    Customer-reported and customer-impacting incidents are in scope at ANY
    severity. IcM files every customer-reported incident as Sev 4, so if the
    severity branch were tested first they would all fall through to '' and be
    dropped -- silently.
    """
    query = build_watchlist_query(scope, "IncidentsSnapshotV2", 30)
    reported = query.index("'customer-reported'")
    impacting = query.index("'customer-impacting'")
    severity = query.index("'sev2-or-2.5-not-auto'")
    assert reported < severity
    assert impacting < severity


def test_customer_reported_value_has_no_space(scope):
    """'Customer Reported' is a value IcM never writes.

    An earlier fleet config used the spaced form and therefore matched nothing,
    which meant customer-reported incidents were silently absent from every
    measurement taken with it.
    """
    query = build_watchlist_query(scope, "IncidentsSnapshotV2", 30)
    assert "IncidentType == 'CustomerReported'" in query
    assert "'Customer Reported'" not in query


def test_auto_mitigation_is_an_identity_test(scope):
    """Not a duration test.

    A duration proxy both admits slow automated recoveries and excludes fast
    human ones.
    """
    query = build_watchlist_query(scope, "IncidentsSnapshotV2", 30)
    assert "MitigatedBy in ('healthmanagesvc', 'm365trustautomation')" in query
    assert "isnotnull(MitigateDate) and MitigatedBy in" in query


def test_unclassified_environment_is_tracked_not_dropped(scope):
    query = build_watchlist_query(scope, "IncidentsSnapshotV2", 30)
    assert "'sev2-or-2.5-unclassified-env'" in query


def test_classifier_treats_dprodmgd_as_production(scope):
    """DPRODMGD* are dedicated production managed farms.

    Filtering on OccurringEnvironment == 'PROD' alone drops them, which loses
    the MicroservicePing incidents entirely.
    """
    classifier = build_environment_classifier(scope)
    assert "Env startswith 'DPRODMGD'" in classifier
    assert classifier.rstrip().endswith("'UNCLASSIFIED')")


def test_classifier_lowercases_title_tokens(scope):
    # The column is compared as tolower(Title), so an upper-case token would
    # never match.
    classifier = build_environment_classifier(scope)
    assert "TitleLower contains '(sdf)'" in classifier
    assert "TitleLower contains '(SDF)'" not in classifier


def test_lookback_is_parameterised(scope):
    assert "ago(30d)" in build_watchlist_query(scope, "IncidentsSnapshotV2", 30)
    assert "ago(7d)" in build_watchlist_query(scope, "IncidentsSnapshotV2", 7)


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="1",
        title="t",
        severity=2.0,
        severity_raw=2,
        status="ACTIVE",
        incident_type="LiveSite",
        track_reason="sev2-or-2.5-not-auto",
        monitor_id="m",
        owning_team_id="104519",
        owning_team_name="team",
        owning_contact_alias="a",
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


def test_sort_puts_customer_impact_first_then_severity():
    ordered = sort_incidents(
        [
            _incident(incident_id="sev2", severity=2.0),
            _incident(incident_id="cust-sev4", severity=4.0, is_customer_impacting=True),
            _incident(incident_id="sev2.5", severity=2.5),
        ]
    )
    assert [i.incident_id for i in ordered] == ["cust-sev4", "sev2", "sev2.5"]


def test_sev_two_point_five_outranks_sev_three():
    ordered = sort_incidents(
        [_incident(incident_id="three", severity=3.0), _incident(incident_id="two-five", severity=2.5)]
    )
    assert [i.incident_id for i in ordered] == ["two-five", "three"]


def test_staleness_threshold_is_seven_days():
    assert not _incident(minutes_open=167 * 60).is_stale
    assert _incident(minutes_open=168 * 60).is_stale
