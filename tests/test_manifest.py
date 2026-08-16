"""The payload manifest.

An address book, not a briefing. The tests here hold two properties: every
value is traceable to a source, and every step carries enough to be invoked
without the agent inferring anything.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from oce_sentry.manifest import (
    SCHEMA,
    build_manifest,
    extract_caveat,
    manifest_path,
    parse_base_rate,
    render,
)
from oce_sentry.models import Incident
from oce_sentry.payload import QueryItem, Selection, SkillItem, resolve_window

NOW = datetime(2026, 8, 16, 1, 0, 0, tzinfo=timezone.utc)


class _Policy:
    icm = {
        "cluster": "https://icmcluster.kusto.windows.net",
        "database": "IcmDataWarehouse",
        "tables": {"incidents": "IncidentsSnapshotV2"},
    }


class _Config:
    policy = _Policy()

    def __init__(self, tmp_path: Path | None = None):
        self.output_dir = tmp_path or Path(".")


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="841552464", title="[Failed Ping Alert] Unable to reach MEDIA",
        severity=2.0, severity_raw=2, status="ACTIVE", incident_type="LiveSite",
        track_reason="sev2-or-2.5-not-auto", monitor_id="MicroservicePing",
        owning_team_id="104519", owning_team_name="SHAREPOINTSNAP\\MeTAWeb",
        owning_contact_alias="", create_date="2026-07-27T09:30:12.677Z",
        mitigate_date=None, mitigated_by=None, is_terminal=False,
        minutes_open=29100.0, is_customer_impacting=False, env_class="PROD",
        tsg_id="",
    )
    base.update(kwargs)
    return Incident(**base)


def _query(**kwargs) -> QueryItem:
    base = dict(
        kit_id="failed-ping-4d3a2e",
        cluster="https://odspmicroservices.eastus2.kusto.windows.net",
        database="odspmediaprod",
        kql="// CAVEAT: an empty error signal here is evidence.\nPlatformEvent | take 10",
    )
    base.update(kwargs)
    return QueryItem(**base)


def _skill(**kwargs) -> SkillItem:
    base = dict(
        skill_id="network", name="network", description="Investigates DNS failures.",
        directory=Path("C:/repos/SRELivesite-RCAAgent/skills/network"),
        source_repo="SRELivesite-RCAAgent",
    )
    base.update(kwargs)
    return SkillItem(**base)


def _manifest(selection: Selection, incident: Incident | None = None) -> dict:
    incident = incident or _incident()
    return build_manifest(
        incident, selection, _Config(),
        window=resolve_window(incident, now=NOW),
        generated_at="2026-08-16T01:00:00Z",
    )


# ------------------------------------------------------------------- shape


def test_it_is_valid_json_and_declares_its_schema():
    body = render(_manifest(Selection(queries=[_query()])))
    loaded = json.loads(body)
    assert loaded["schema"] == SCHEMA


def test_steps_are_numbered_in_order():
    manifest = _manifest(Selection(queries=[_query(), _query(kit_id="b")], skills=[_skill()]))
    assert [s["order"] for s in manifest["steps"]] == [1, 2, 3]


def test_queries_come_before_skills():
    """Measure, then reason. The only opinion in the file, and it is structural."""
    manifest = _manifest(Selection(queries=[_query()], skills=[_skill()]))
    assert [s["type"] for s in manifest["steps"]] == ["query", "skill"]


def test_an_empty_selection_still_describes_the_incident():
    manifest = _manifest(Selection())
    assert manifest["steps"] == []
    assert manifest["incident"]["id"] == "841552464"


# ---------------------------------------------------------------- invocable


def test_a_query_step_carries_everything_needed_to_call_it():
    """An agent should not have to infer the calling convention."""
    step = _manifest(Selection(queries=[_query()]))["steps"][0]
    assert step["run"]["via"] == "mcp"
    assert step["run"]["server"] == "azure"
    assert step["run"]["tool"] == "kusto_query"
    arguments = step["run"]["arguments"]
    assert arguments["cluster-uri"].startswith("https://")
    assert arguments["database"]
    assert "PlatformEvent" in arguments["query"]


def test_a_query_step_offers_a_browser_alternate():
    step = _manifest(Selection(queries=[_query()]))["steps"][0]
    assert step["alternate"]["via"] == "browser"
    assert step["alternate"]["url"].startswith("https://dataexplorer.azure.com/")


def test_a_skill_step_points_at_a_file():
    step = _manifest(Selection(skills=[_skill()]))["steps"][0]
    assert step["run"]["via"] == "file"
    assert step["run"]["path"].endswith("SKILL.md")
    assert step["source"]["repo"] == "SRELivesite-RCAAgent"


# ----------------------------------------------------------------- capture


def test_the_kits_caveat_travels_with_the_query():
    """It is the fleet's own warning about misreading this query."""
    step = _manifest(Selection(queries=[_query()]))["steps"][0]
    assert "empty error signal" in step["caveat"]


def test_no_caveat_is_null_not_invented():
    step = _manifest(Selection(queries=[_query(kql="T | take 1")]))["steps"][0]
    assert step["caveat"] is None


def test_a_redacted_tsg_is_null_but_the_raw_value_is_kept():
    """The agent needs to know there is no link; an auditor needs to know why."""
    manifest = _manifest(Selection(), incident=_incident(tsg_id="** REDACTED **"))
    assert manifest["incident"]["tsgUrl"] is None
    assert manifest["incident"]["tsgRaw"] == "** REDACTED **"


def test_a_real_tsg_appears_as_a_url():
    manifest = _manifest(Selection(), incident=_incident(tsg_id="https://eng.ms/tsg"))
    assert manifest["incident"]["tsgUrl"] == "https://eng.ms/tsg"


def test_missing_values_are_null_rather_than_blank():
    manifest = _manifest(Selection())
    assert manifest["incident"]["owningContactAlias"] is None
    assert manifest["incident"]["description"] is None


def test_the_incident_records_where_it_came_from():
    source = _manifest(Selection())["incident"]["source"]
    assert source["cluster"] == "https://icmcluster.kusto.windows.net"
    assert source["table"] == "IncidentsSnapshotV2"


def test_the_window_records_how_it_was_derived():
    window = _manifest(Selection())["window"]
    assert window["start"] == "2026-07-27T09:30:12Z"
    assert window["end"] == "2026-08-16T01:00:00Z"
    assert window["derivedFrom"] == ["CreateDate", "MitigateDate"]


# --------------------------------------------------------------- base rate

_CARD = """## Base rate

| Measure | Value |
|---|---|
| Firings in window | **8** (0.62/week) |
| Trend | rising (1.82x versus the prior window) |
| Closed by automation | **37.5%** (3 of 8) |
| Distinct signatures | 1 |
"""


def test_base_rate_measures_are_captured():
    parsed = parse_base_rate(_CARD)
    assert parsed["firings"] == "8 (0.62/week)"
    assert parsed["closedByAutomation"] == "37.5% (3 of 8)"
    assert parsed["distinctSignatures"] == "1"


def test_measures_keep_their_denominator():
    """"37.5%" without "(3 of 8)" hides how small the sample is."""
    assert "(3 of 8)" in parse_base_rate(_CARD)["closedByAutomation"]


def test_an_absent_card_yields_no_base_rate():
    assert parse_base_rate("") == {}
    step = _manifest(Selection(queries=[_query()]))["steps"][0]
    assert step["evidence"]["baseRate"] is None


def test_the_card_is_referenced_by_path_not_inlined(tmp_path):
    """120 lines of prose in a manifest is a document, not an address book."""
    query = _query(directory=tmp_path)
    step = _manifest(Selection(queries=[query]))["steps"][0]
    assert step["evidence"]["cardPath"].endswith("README.md")
    assert step["evidence"]["queryPath"].endswith("investigate.kql")


# ---------------------------------------------------------------- caveat re


def test_extract_caveat_reads_only_the_marked_comment():
    assert extract_caveat("// CAVEAT: mind the gap\nT | take 1") == "mind the gap"
    assert extract_caveat("// just a comment\nT | take 1") is None
    assert extract_caveat("") is None


# ---------------------------------------------------------------- resources


def test_clusters_say_which_steps_use_them():
    manifest = _manifest(Selection(queries=[_query(), _query(kit_id="b")]))
    cluster = manifest["resources"]["clusters"][0]
    assert cluster["usedBySteps"] == [1, 2]


def test_auth_is_stated_once():
    assert _manifest(Selection())["resources"]["auth"] == "az login"


def test_connectors_include_what_the_skills_need(tmp_path):
    """Deriving connectors from queries alone left the address book wrong.

    `network` names geneva-mcp and workiq in its own instructions; a manifest
    listing only `azure` tells the agent it has everything when it does not.
    """

    class _Connector:
        def __init__(self, name, required_by):
            self.name = name
            self.kind = "stdio"
            self.target = f"agency mcp {name}"
            self.status = "ready"
            self.purpose = ""
            self.required_by = required_by

    connectors = [
        _Connector("azure", ["network"]),
        _Connector("geneva-mcp", ["network"]),
        _Connector("workiq", ["network"]),
        _Connector("drdashboard", ["something-else"]),
    ]
    incident = _incident()
    manifest = build_manifest(
        incident,
        Selection(skills=[_skill()]),
        _Config(),
        connectors=connectors,
        window=resolve_window(incident, now=NOW),
    )
    listed = {c["name"] for c in manifest["resources"]["connectors"]}
    assert listed == {"azure", "geneva-mcp", "workiq"}
    assert "drdashboard" not in listed


def test_a_connector_says_which_skills_named_it():
    class _Connector:
        name = "geneva-mcp"
        kind = "stdio"
        target = "dnx GenevaMonitoring.MCP.Server"
        status = "ready"
        purpose = "Geneva monitor health"
        required_by = ["network", "redis"]

    incident = _incident()
    manifest = build_manifest(
        incident,
        Selection(skills=[_skill()]),
        _Config(),
        connectors=[_Connector()],
        window=resolve_window(incident, now=NOW),
    )
    assert manifest["resources"]["connectors"][0]["namedBySkills"] == ["network"]


def test_the_path_is_stable(tmp_path):
    incident = _incident()
    config = _Config(tmp_path)
    assert manifest_path(incident, config) == manifest_path(incident, config)
    assert manifest_path(incident, config).name == "payload.json"
