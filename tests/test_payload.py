"""The agent payload.

Sentry aggregates and hands off; it does not investigate. These tests hold the
two properties that makes the handoff worth trusting: it is reproducible, and
it never emits a query the agent would have to finish.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oce_sentry.models import Incident
from oce_sentry.payload import (
    END,
    START,
    QueryItem,
    Selection,
    SkillItem,
    WindowError,
    build_payload,
    fingerprint,
    has_placeholders,
    payload_path,
    resolve_query,
    resolve_window,
    trim_base_rate,
)

NOW = datetime(2026, 8, 16, 1, 0, 0, tzinfo=timezone.utc)


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="836736526",
        title="[Sev3 Alertstorm] 159 Sev3s",
        severity=2.0,
        severity_raw=2,
        status="ACTIVE",
        incident_type="LiveSite",
        track_reason="sev2-or-2.5-unclassified-env",
        monitor_id="ODSPSev3Alertstorm",
        owning_team_id="104519",
        owning_team_name="SHAREPOINTSNAP\\MeTAAnalysis",
        owning_contact_alias="sajosep",
        create_date="2026-07-18T14:56:00.133Z",
        mitigate_date=None,
        mitigated_by=None,
        is_terminal=False,
        minutes_open=41000.0,
        is_customer_impacting=False,
        env_class="UNCLASSIFIED",
        tsg_id="",
    )
    base.update(kwargs)
    return Incident(**base)


def _query(**kwargs) -> QueryItem:
    base = dict(
        kit_id="sev3-alertstorm",
        cluster="https://icmcluster.kusto.windows.net",
        database="IcmDataWarehouse",
        kql="IncidentsSnapshotV2 | where CreateDate between (datetime(a) .. datetime(b))",
    )
    base.update(kwargs)
    return QueryItem(**base)


def _skill(**kwargs) -> SkillItem:
    base = dict(
        skill_id="outage-pattern",
        name="outage-pattern",
        description="Detects wider outages.",
        directory=Path("C:/repos/SRELivesite-RCAAgent/skills/outage-pattern"),
        source_repo="SRELivesite-RCAAgent",
    )
    base.update(kwargs)
    return SkillItem(**base)


# --------------------------------------------------------------------- window


def test_an_open_incident_runs_to_now():
    start, end, provenance = resolve_window(_incident(), now=NOW)
    assert start == "2026-07-18T14:56:00Z"
    assert end == "2026-08-16T01:00:00Z"
    assert "still open" in provenance


def test_a_mitigated_incident_ends_when_it_was_mitigated():
    start, end, provenance = resolve_window(
        _incident(mitigate_date="2026-07-19T02:30:00Z"), now=NOW
    )
    assert end == "2026-07-19T02:30:00Z"
    assert provenance == "CreateDate .. MitigateDate"


def test_an_inverted_window_is_refused():
    """Zero rows from an inverted window reads as an all-clear.

    The kit's own runner refuses for the same reason; emitting the query
    anyway would hand the agent something that looks healthy and is not.
    """
    with pytest.raises(WindowError, match="inverted"):
        resolve_window(
            _incident(create_date="2026-07-19T00:00:00Z", mitigate_date="2026-07-18T00:00:00Z")
        )


def test_a_zero_length_window_is_refused():
    with pytest.raises(WindowError):
        resolve_window(
            _incident(create_date="2026-07-18T00:00:00Z", mitigate_date="2026-07-18T00:00:00Z")
        )


def test_a_missing_create_date_is_refused():
    with pytest.raises(WindowError, match="CreateDate"):
        resolve_window(_incident(create_date=""))


def test_offsets_are_normalised_to_utc():
    start, _, _ = resolve_window(_incident(create_date="2026-07-18T10:56:00-04:00"), now=NOW)
    assert start == "2026-07-18T14:56:00Z"


# ---------------------------------------------------------------- resolution


def test_placeholders_are_substituted():
    kql = f"T | where Time between ({START} .. {END})"
    resolved = resolve_query(kql, "2026-07-18T14:56:00Z", "2026-08-16T01:00:00Z")
    assert not has_placeholders(resolved)
    assert "datetime(2026-07-18T14:56:00Z)" in resolved
    assert "datetime(2026-08-16T01:00:00Z)" in resolved


def test_the_obsolete_instruction_is_replaced():
    """Substituting inside the comment produced nonsense.

    "Replace datetime(2026-07-18T14:56:00Z) with the incident window"
    describes work that is already done.
    """
    kql = (
        f"// Replace {START} and {END} with the incident window, or use\n"
        "// run.ps1 which does it for you.\n"
        f"T | where Time between ({START} .. {END})"
    )
    resolved = resolve_query(kql, "2026-07-18T14:56:00Z", "2026-08-16T01:00:00Z")
    assert "Replace" not in resolved
    assert "Window substituted by OCE Sentry" in resolved
    assert not has_placeholders(resolved)


def test_a_query_without_placeholders_is_untouched():
    kql = "T | take 10"
    assert resolve_query(kql, "a", "b") == kql


# -------------------------------------------------------------------- payload


def test_the_payload_is_reproducible():
    """The same incident and selection must produce the same bytes.

    Determinism is the claim this tool makes; a digest is how anyone checks it.
    """
    incident = _incident()
    selection = Selection(queries=[_query()], skills=[_skill()])
    window = resolve_window(incident, now=NOW)
    first = build_payload(incident, selection, window=window)
    second = build_payload(incident, selection, window=window)
    assert fingerprint(first) == fingerprint(second)


def test_the_payload_states_the_window_and_its_source():
    body = build_payload(
        _incident(), Selection(queries=[_query()]), window=resolve_window(_incident(), now=NOW)
    )
    assert "2026-07-18T14:56:00Z" in body
    assert "2026-08-16T01:00:00Z" in body
    assert "still open" in body


def test_the_payload_names_where_a_skill_lives():
    """The agent has to load it from disk, so the path is not optional."""
    body = build_payload(
        _incident(), Selection(skills=[_skill()]), window=resolve_window(_incident(), now=NOW)
    )
    assert "outage-pattern" in body
    assert "SKILL.md" in body
    assert "SRELivesite-RCAAgent" in body


def test_the_payload_carries_cluster_and_database():
    body = build_payload(
        _incident(), Selection(queries=[_query()]), window=resolve_window(_incident(), now=NOW)
    )
    assert "icmcluster.kusto.windows.net" in body
    assert "IcmDataWarehouse" in body


def test_an_empty_selection_still_produces_a_usable_payload():
    """The incident facts alone are worth handing over."""
    body = build_payload(
        _incident(), Selection(), window=resolve_window(_incident(), now=NOW)
    )
    assert "836736526" in body
    assert "None selected." in body


def test_a_redacted_tsg_is_not_presented_as_a_link():
    """IcM stores '** REDACTED **' for MSRC incidents."""
    body = build_payload(
        _incident(tsg_id="** REDACTED **"),
        Selection(),
        window=resolve_window(_incident(), now=NOW),
    )
    assert "REDACTED" not in body


def test_a_real_tsg_is_included():
    body = build_payload(
        _incident(tsg_id="https://eng.ms/docs/tsg"),
        Selection(),
        window=resolve_window(_incident(), now=NOW),
    )
    assert "https://eng.ms/docs/tsg" in body


def test_the_payload_says_it_did_not_interpret_anything():
    body = build_payload(
        _incident(), Selection(), window=resolve_window(_incident(), now=NOW)
    )
    assert "does not investigate" in body


def test_the_path_is_stable_per_incident(tmp_path):
    """The operator points their agent at a file; it must not move."""

    class _Config:
        output_dir = tmp_path

    first = payload_path(_incident(), _Config())
    second = payload_path(_incident(), _Config())
    assert first == second
    assert first.name == "payload.md"
    assert first.parent.name == "836736526"


# ---------------------------------------------------------------- base rates

_CARD = """# [Failed Ping Alert] Unable to reach MEDIA

**Cluster** `4d3a2e` | **action level** signature

## Base rate - what this condition normally does

| Measure | Value |
|---|---|
| Firings in window | **8** |

### What the base rate implies for confidence

- Automation closes 12.5% of firings.

## Run the investigation

```powershell
.\\run.ps1 -IncidentId 848732156
```

## Recent members

- [848732156](https://portal.microsofticm.com/imp/v3/incidents/details/848732156/home)

## Ownership

Team: SHAREPOINTSNAP\\MeTAWeb
"""


def test_the_card_keeps_its_evidence():
    trimmed = trim_base_rate(_CARD)
    assert "Firings in window" in trimmed
    assert "implies for confidence" in trimmed
    assert "Recent members" in trimmed
    assert "Ownership" in trimmed


def test_the_card_drops_run_instructions_naming_another_incident():
    """The card ships a worked example naming whichever incident was current
    when it was generated. Carried into a payload, that invites an agent to
    investigate a different incident than the one it was handed.
    """
    trimmed = trim_base_rate(_CARD)
    assert "run.ps1" not in trimmed
    assert "Run the investigation" not in trimmed
    # The id survives as evidence under Recent members, which is correct --
    # what must not survive is the instruction to go and run it.
    assert ".\\run.ps1 -IncidentId 848732156" not in trimmed


def test_an_empty_card_trims_to_nothing():
    assert trim_base_rate("") == ""
    assert trim_base_rate("# Heading only\n") == ""


def test_the_payload_never_tells_the_agent_to_run_a_kit_script():
    body = build_payload(
        _incident(),
        Selection(queries=[_query(base_rate_card=_CARD)]),
        window=resolve_window(_incident(), now=NOW),
    )
    assert "run.ps1" not in body


def test_the_payload_names_the_tool_that_runs_the_query():
    """"Run the queries" is not actionable without saying how."""
    body = build_payload(
        _incident(), Selection(queries=[_query()]), window=resolve_window(_incident(), now=NOW)
    )
    assert "kusto_query" in body
    assert "cluster-uri" in body
    assert "azure" in body
