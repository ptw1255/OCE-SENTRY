"""Bug drafting, board mapping and the ADO write contract."""

from __future__ import annotations

import json

import pytest

from oce_sentry.ado import DEFAULT_BOARD, AdoClient, Bug, derive_monitor_id, load_board
from oce_sentry.bugs import (
    CATEGORIES,
    BugDraft,
    BugDraftError,
    _fallback_body,
    _fallback_title,
    parse_draft,
)
from oce_sentry.models import Incident


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="836736526",
        title="[Sev3 Alertstorm] 159 Sev3s fired",
        severity=2.0,
        severity_raw=2,
        status="ACTIVE",
        incident_type="LiveSite",
        track_reason="sev2-or-2.5-unclassified-env",
        monitor_id="ODSPSev3Alertstorm",
        owning_team_id="104519",
        owning_team_name="MeTA Analysis OCE",
        owning_contact_alias="sajosep",
        create_date="2026-07-18T14:56:00Z",
        mitigate_date=None,
        mitigated_by=None,
        is_terminal=False,
        minutes_open=40000.0,
        is_customer_impacting=False,
        env_class="UNCLASSIFIED",
        tsg_id="https://eng.ms/tsg",
    )
    base.update(kwargs)
    return Incident(**base)


# ------------------------------------------------------------------- board


def test_bugs_land_where_the_fleet_files_them():
    """Same board, same tag, so one query finds every noise bug.

    An operator's bug that lands somewhere else is a bug nobody reviews.
    """
    assert DEFAULT_BOARD["organization"] == "onedrive"
    assert DEFAULT_BOARD["project"] == "OneBranch"
    assert DEFAULT_BOARD["areaPath"] == "OneBranch\\NEXUS\\MeTA"
    assert DEFAULT_BOARD["tag"] == "meta-monitor-noise"


def test_the_owner_is_pinned():
    assert DEFAULT_BOARD["assignedTo"] == "parkerwall@microsoft.com"


def test_board_is_overridable_without_a_code_change(tmp_path, monkeypatch):
    path = tmp_path / "board.json"
    path.write_text(json.dumps({"project": "OtherProject"}), encoding="utf-8")
    monkeypatch.setenv("OCE_SENTRY_ADO_BOARD", str(path))

    board = load_board()
    assert board["project"] == "OtherProject"
    # Unspecified keys keep their defaults rather than vanishing.
    assert board["areaPath"] == DEFAULT_BOARD["areaPath"]


# ------------------------------------------------------------------- draft


def test_draft_is_split_into_title_and_body():
    title, body = parse_draft("TITLE: Monitor noise: X fires a lot\n---\n<p>Because.</p>")
    assert title == "Monitor noise: X fires a lot"
    assert body == "<p>Because.</p>"


def test_draft_tolerates_a_code_fence():
    # A model wrapping its reply in a fence should not cost the operator
    # their note.
    title, body = parse_draft("```\nTITLE: A thing\n---\n<p>Body.</p>\n```")
    assert title == "A thing"
    assert body == "<p>Body.</p>"


def test_draft_without_a_title_is_rejected():
    with pytest.raises(BugDraftError, match="no TITLE"):
        parse_draft("<p>Just a body.</p>")


def test_draft_without_a_body_is_rejected():
    with pytest.raises(BugDraftError, match="no body"):
        parse_draft("TITLE: Only a title\n---\n")


def test_fallback_preserves_the_operators_words_verbatim():
    """Losing a model must not mean losing the observation.

    The note is what the engineer actually said; the drafted prose is a summary
    of it, and the summary is the part that can be wrong.
    """
    note = "The TSG links to a dashboard that no longer exists."
    body = _fallback_body(note, "tsg", _incident())
    assert note in body
    assert "836736526" in body
    assert "not a summary" in body


def test_fallback_title_is_categorised():
    assert _fallback_title("x", "noise").startswith("Monitor noise:")
    assert _fallback_title("x", "tsg").startswith("TSG gap:")
    assert _fallback_title("x", "other").startswith("Operator report:")


def test_fallback_title_is_bounded():
    assert len(_fallback_title("y" * 400, "noise")) < 130


def test_categories_cover_the_asked_for_cases():
    keys = {key for key, _ in CATEGORIES}
    assert {"noise", "tsg"} <= keys


# -------------------------------------------------------------------- write


def test_create_payload_carries_owner_area_and_tags():
    board = load_board()
    payload = AdoClient.create_bug(
        AdoClient.__new__(AdoClient),
        board=board,
        title="T",
        description_html="<p>B</p>",
        extra_tags=["oce-tsg"],
        dry_run=True,
    )
    ops = {op["path"]: op["value"] for op in payload["operations"]}
    assert ops["/fields/System.AssignedTo"] == "parkerwall@microsoft.com"
    assert ops["/fields/System.AreaPath"] == "OneBranch\\NEXUS\\MeTA"
    assert ops["/fields/Microsoft.VSTS.TCM.ReproSteps"] == "<p>B</p>"

    tags = ops["/fields/System.Tags"]
    assert "meta-monitor-noise" in tags  # joins the fleet's bugs
    assert "oce-sentry" in tags  # but stays distinguishable within them
    assert "oce-tsg" in tags


def test_dry_run_creates_nothing():
    payload = AdoClient.create_bug(
        AdoClient.__new__(AdoClient),
        board=load_board(),
        title="T",
        description_html="<p>B</p>",
        dry_run=True,
    )
    assert payload["dryRun"] is True
    assert "id" not in payload


# ------------------------------------------------------------------ tracking


def _bug(**kwargs) -> Bug:
    base = dict(
        id=1,
        title="Monitor noise: LSLA013 - [Stream] Video PreTranscode fires 900 times in 30 days",
        state="New",
        assigned_to="Parker Wall",
        tags=["meta-monitor-noise"],
        created="2026-08-01T00:00:00Z",
        changed="2026-08-01T00:00:00Z",
    )
    base.update(kwargs)
    return Bug(**base)


def test_terminal_states_stop_counting_as_open():
    assert _bug(state="Done").is_terminal
    assert _bug(state="Removed").is_terminal
    assert not _bug(state="New").is_terminal
    assert not _bug(state="Committed").is_terminal


def test_console_filed_bugs_are_distinguishable():
    assert _bug(tags=["meta-monitor-noise", "oce-sentry"]).from_console
    assert not _bug(tags=["meta-monitor-noise"]).from_console


def test_idle_is_measured_from_the_last_change_not_the_filing():
    """A bug filed long ago but touched yesterday is being worked."""
    old_but_active = _bug(created="2026-01-01T00:00:00Z", changed="2026-08-15T00:00:00Z")
    assert (old_but_active.age_days() or 0) > (old_but_active.idle_days() or 0)


def test_monitor_id_is_recovered_from_the_title():
    assert (
        derive_monitor_id(
            "Monitor noise: LSLA013 - [Stream] Video PreTranscode fires 900 times in 30 days"
        )
        == "LSLA013 - [Stream] Video PreTranscode"
    )


def test_a_free_text_title_does_not_produce_a_bogus_monitor():
    """A guessed monitor id would join a bug to the wrong incident."""
    assert derive_monitor_id("TSG gap: no threshold documented") == ""
    assert derive_monitor_id("") == ""

