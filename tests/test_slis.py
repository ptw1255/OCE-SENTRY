"""SLI registry, query construction and error-budget arithmetic."""

from __future__ import annotations

import json

import pytest

from oce_sentry.sources.slis import (
    DEFAULT_SLIS,
    META_SERVICE_TREE_ID,
    Sli,
    SliSlice,
    SliWindow,
    build_slice_query,
    build_summary_query,
    build_trend_query,
    load_registry,
)


def _sli(**kwargs) -> Sli:
    base = dict(
        id="x",
        name="X",
        table="t",
        objective=99.9,
        denominator=1_000_000.0,
        numerator=999_000.0,
    )
    base.update(kwargs)
    return Sli(**base)


def test_two_slis_are_registered_by_default():
    ids = {entry["id"] for entry in DEFAULT_SLIS}
    assert ids == {"analysis-reliability", "web-reliability"}


def test_tables_are_keyed_by_service_tree_id():
    """slidata tables are named by ServiceTreeId, never by service name.

    A name-based search of that database finds nothing and wrongly concludes
    the service has no SLOs -- which is exactly what happened during the
    original investigation.
    """
    for entry in DEFAULT_SLIS:
        assert entry["table"].startswith(META_SERVICE_TREE_ID)
        assert ".RawData.SuccessRateSLOs." in entry["table"]


def test_version_is_pinned_not_discovered():
    """A new Ver starts a new table and the old one stops receiving windows.

    Following "highest Ver" automatically would silently change what is being
    measured without anyone deciding to.
    """
    assert all(entry["table"].endswith(".Ver3") for entry in DEFAULT_SLIS)


def test_table_names_are_bracket_quoted():
    # Names contain dots and spaces, so a bare reference is a syntax error.
    query = build_summary_query(DEFAULT_SLIS[0]["table"], 24)
    assert query.startswith("['")
    assert "']" in query


def test_queries_are_scoped_to_the_window():
    assert "ago(24h)" in build_summary_query("t", 24)
    assert "ago(168h)" in build_trend_query("t", 168, 60)
    assert "ago(1h)" in build_slice_query("t", 1, "LocationId")


def test_environment_and_region_use_the_documented_columns():
    assert "tostring(CustomerResourceId)" in build_slice_query("t", 24, "CustomerResourceId")
    assert "tostring(LocationId)" in build_slice_query("t", 24, "LocationId")


def test_value_is_the_ratio_geneva_recorded():
    assert _sli(denominator=1000, numerator=999).value == pytest.approx(99.9)


def test_error_budget_burn_exposes_what_the_percentage_hides():
    """99.87% against a 99.9% objective reads fine and is over budget.

    The percentage is the number people quote; the budget is the number that
    decides whether to act.
    """
    sli = _sli(objective=99.9, denominator=1_000_000, numerator=998_700)
    assert sli.value == pytest.approx(99.87)
    assert sli.error_budget_burn == pytest.approx(130.0)
    assert sli.meeting_objective is False


def test_a_healthy_sli_is_under_budget():
    # 999,500 of 1,000,000 is 99.95%: half of the 0.1% budget consumed.
    sli = _sli(objective=99.9, denominator=1_000_000, numerator=999_500)
    assert sli.meeting_objective is True
    assert sli.error_budget_burn == pytest.approx(50.0)


def test_no_traffic_is_not_a_false_zero():
    sli = _sli(denominator=0, numerator=0)
    assert sli.value is None
    assert sli.meeting_objective is None
    assert sli.error_budget_burn is None


def test_slice_value_matches_the_same_arithmetic():
    assert SliSlice(key="prod", denominator=2000, numerator=1999).value == pytest.approx(99.95)


def test_window_value_handles_an_empty_denominator():
    assert SliWindow(start="", denominator=0, numerator=0).value is None


def test_registry_can_be_overridden_without_a_code_change(tmp_path, monkeypatch):
    """Adding an SLI must never require a release.

    Two are registered today; the point of the registry is the third.
    """
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"slis": [{"id": "new", "name": "New SLI", "table": "some.table.Ver1"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OCE_SENTRY_SLI_REGISTRY", str(path))

    registry = load_registry()
    assert [e["id"] for e in registry] == ["new"]
    assert registry[0]["objective"] == 99.9  # defaulted


def test_a_malformed_registry_is_rejected_loudly(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"slis": [{"id": "broken"}]}), encoding="utf-8")
    monkeypatch.setenv("OCE_SENTRY_SLI_REGISTRY", str(path))
    with pytest.raises(ValueError, match="missing 'name'"):
        load_registry()


def test_window_labels_follow_sre_convention():
    from oce_sentry.tui.sli_screen import WINDOWS, _window_label

    # 24h reads as 24h, not 1d; days only from 48h up.
    assert [_window_label(h) for h in WINDOWS] == ["1h", "6h", "24h", "3d", "7d", "30d"]


def test_a_missing_registry_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OCE_SENTRY_SLI_REGISTRY", str(tmp_path / "nope.json"))
    with pytest.raises(ValueError, match="could not be read"):
        load_registry()
