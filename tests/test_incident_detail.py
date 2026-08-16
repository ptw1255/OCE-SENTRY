"""Incident detail: the description and the opened-at timestamp."""

from __future__ import annotations

import pytest

from oce_sentry.models import Incident, _html_to_text


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="850000001",
        title="t",
        severity=2.0,
        severity_raw=2,
        status="ACTIVE",
        incident_type="LiveSite",
        track_reason="r",
        monitor_id="m",
        owning_team_id="1",
        owning_team_name="T",
        owning_contact_alias="a",
        create_date="2026-07-22T23:16:27.743Z",
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


# ------------------------------------------------------------------ opened at


def test_opened_at_is_readable():
    """"How long" and "since when" are different questions."""
    assert _incident().opened_at == "2026-07-22 23:16 UTC"


def test_opened_at_handles_an_offset():
    assert _incident(create_date="2026-07-22T19:16:27-04:00").opened_at == "2026-07-22 23:16 UTC"


def test_opened_at_degrades_rather_than_raising():
    """An unparseable date still tells the operator something."""
    assert _incident(create_date="not a date at all").opened_at == "not a date at al"
    assert _incident(create_date="").opened_at == ""


# ----------------------------------------------------------------- html to text


def test_tags_are_stripped():
    assert _html_to_text("<p>Hello <b>world</b></p>") == "Hello world"


def test_block_tags_become_line_breaks():
    """Otherwise an email arrives as one unreadable paragraph."""
    assert _html_to_text("<p>One</p><p>Two</p>") == "One\n\nTwo"
    assert _html_to_text("First<br>Second") == "First\nSecond"


def test_entities_are_unescaped():
    assert _html_to_text("a &amp; b &nbsp;c &lt;tag&gt;") == "a & b  c <tag>"


def test_runs_of_blank_lines_collapse():
    """IcM summaries are often pasted email, which arrives padded."""
    assert _html_to_text("<p>A</p><p></p><p></p><p></p><p>B</p>") == "A\n\nB"


def test_empty_and_missing_html_are_empty():
    assert _html_to_text("") == ""
    assert _incident(summary="").description == ""


def test_a_real_outlook_fragment_flattens():
    html = (
        '<div data-olk-copy-source="MessageBody" style="border: 0; font-size: 12pt">'
        "<p><strong>Was it ODC convergence related (Y/N)?</strong>&nbsp;</p>"
        "<p>Please provide as much information as possible.</p></div>"
    )
    text = _html_to_text(html)
    assert "<" not in text
    assert "style=" not in text
    assert text.startswith("Was it ODC convergence related (Y/N)?")
    assert "Please provide as much information as possible." in text


def test_description_reads_through_the_summary_field():
    assert _incident(summary="<p>Thumbnails fail</p>").description == "Thumbnails fail"


def test_description_is_absent_not_broken_when_icm_has_none():
    """Monitor-filed incidents routinely carry nothing."""
    assert _incident().description == ""


def test_square_brackets_survive_for_the_caller_to_escape():
    """Textual would eat these as markup; escaping is the UI's job, not the
    model's, so the text arrives intact."""
    assert _html_to_text("<p>See [MeTA] logs</p>") == "See [MeTA] logs"


# -------------------------------------------------------------------- from_row


def test_summary_is_read_from_the_row():
    incident = Incident.from_row(
        {
            "IncidentId": "1",
            "Title": "t",
            "SevNorm": 2.0,
            "Severity": 2,
            "Status": "ACTIVE",
            "IncidentType": "LiveSite",
            "TrackReason": "r",
            "MonitorId": "m",
            "OwningTeamId": "1",
            "OwningTeamName": "T",
            "OwningContactAlias": "a",
            "CreateDate": "2026-07-22T23:16:27.743Z",
            "IsTerminal": False,
            "MinutesOpen": 60.0,
            "IsCustomerImpacting": False,
            "EnvClass": "PROD",
            "TsgId": "",
            "Summary": "<p>Body</p>",
        }
    )
    assert incident.description == "Body"
    assert incident.opened_at == "2026-07-22 23:16 UTC"


def test_a_null_summary_does_not_break_from_row():
    incident = Incident.from_row({"IncidentId": "1", "Summary": None})
    assert incident.description == ""
