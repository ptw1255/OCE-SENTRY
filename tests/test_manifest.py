"""The payload manifest.

Three things and no fourth: where the data is, which skills to run against this
incident and in what order, and where the report goes. The agent reads the
skills themselves -- the manifest does not paraphrase them and carries no
advice about how to investigate.

The tests hold that separation, and that every value is traceable to a source.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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
        self.output_dir = tmp_path or Path("C:/state/output")


class _Connector:
    def __init__(self, name, required_by=(), purpose=""):
        self.name = name
        self.kind = "stdio"
        self.target = f"agency mcp {name}"
        self.status = "ready"
        self.purpose = purpose
        self.required_by = list(required_by)


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


def _manifest(selection: Selection, incident: Incident | None = None, connectors=None) -> dict:
    incident = incident or _incident()
    return build_manifest(
        incident, selection, _Config(), connectors=connectors,
        window=resolve_window(incident, now=NOW),
        generated_at="2026-08-16T01:00:00Z",
    )


# --------------------------------------------------------------------- shape


def test_it_is_valid_json_and_declares_its_schema():
    loaded = json.loads(render(_manifest(Selection(queries=[_query()]))))
    assert loaded["schema"] == SCHEMA


def test_it_has_exactly_the_parts_it_promises():
    """Access, sequence, output. Anything else is scope creep in a data file."""
    manifest = _manifest(Selection(queries=[_query()], skills=[_skill()]))
    assert set(manifest) == {
        "schema", "generatedAt", "generatedBy",
        "incident", "window", "access", "sequence", "output",
    }


def test_it_carries_no_instructions_for_the_agent():
    """The agent reads the skills. The manifest is an address book.

    Earlier versions carried prose telling the agent how to behave -- "ground
    every number in a returned row" -- which is advice, not captured data, and
    duplicates what the skills already say.
    """
    body = render(_manifest(Selection(queries=[_query()], skills=[_skill()]))).lower()
    for phrase in ("ground every", "do not widen", "what to do with this", "follow their"):
        assert phrase not in body


# ------------------------------------------------------------------ sequence


def test_the_sequence_is_skills_in_the_operators_order():
    manifest = _manifest(
        Selection(skills=[_skill(skill_id="icm"), _skill(skill_id="network")])
    )
    assert [(s["order"], s["skillId"]) for s in manifest["sequence"]] == [
        (1, "icm"),
        (2, "network"),
    ]


def test_queries_are_not_in_the_sequence():
    """A query is data access, not a step the agent is told to perform."""
    manifest = _manifest(Selection(queries=[_query()], skills=[_skill()]))
    assert len(manifest["sequence"]) == 1
    assert manifest["sequence"][0]["skillId"] == "network"


def test_each_sequence_entry_points_at_a_skill_file():
    entry = _manifest(Selection(skills=[_skill()]))["sequence"][0]
    assert entry["path"].endswith("SKILL.md")
    assert entry["repo"] == "SRELivesite-RCAAgent"
    assert Path(entry["path"]).is_absolute()


def test_an_empty_selection_still_describes_the_incident():
    manifest = _manifest(Selection())
    assert manifest["sequence"] == []
    assert manifest["access"]["queries"] == []
    assert manifest["incident"]["id"] == "841552464"


# -------------------------------------------------------------------- access


def test_a_query_is_reachable_without_deriving_anything():
    source = _manifest(Selection(queries=[_query()]))["access"]["queries"][0]
    assert source["cluster"].startswith("https://")
    assert source["database"] == "odspmediaprod"
    assert "PlatformEvent" in source["query"]
    assert source["via"] == {"server": "azure", "tool": "kusto_query"}
    assert source["windowSubstituted"] is True


def test_a_query_offers_a_browser_url():
    source = _manifest(Selection(queries=[_query()]))["access"]["queries"][0]
    assert source["explorerUrl"].startswith("https://dataexplorer.azure.com/")


def test_a_query_records_where_it_came_from():
    """Hardcode what the machine already knows rather than making it look."""
    manifest = _manifest(Selection(queries=[_query(directory=Path("C:/kits/a"))]))
    paths = manifest["access"]["queries"][0]["paths"]
    assert paths["query"].endswith("investigate.kql")
    assert paths["baseRateCard"].endswith("README.md")
    assert paths["kit"] == str(Path("C:/kits/a"))


def test_clusters_say_which_queries_use_them():
    manifest = _manifest(Selection(queries=[_query(), _query(kit_id="b")]))
    cluster = manifest["access"]["clusters"][0]
    assert cluster["usedBy"] == ["failed-ping-4d3a2e", "b"]


def test_auth_is_stated_once():
    assert _manifest(Selection())["access"]["auth"] == "az login"


def test_connectors_include_what_the_skills_need():
    """Deriving connectors from queries alone left the address book wrong.

    `network` names geneva-mcp and workiq in its own instructions; a manifest
    listing only `azure` tells the agent it has everything when it does not.
    """
    manifest = _manifest(
        Selection(skills=[_skill()]),
        connectors=[
            _Connector("azure", ["network"]),
            _Connector("geneva-mcp", ["network"]),
            _Connector("workiq", ["network"]),
            _Connector("drdashboard", ["something-else"]),
        ],
    )
    listed = {c["name"] for c in manifest["access"]["connectors"]}
    assert listed == {"azure", "geneva-mcp", "workiq"}


def test_a_connector_says_which_skills_named_it():
    manifest = _manifest(
        Selection(skills=[_skill()]),
        connectors=[_Connector("geneva-mcp", ["network", "redis"])],
    )
    assert manifest["access"]["connectors"][0]["namedBySkills"] == ["network"]


# -------------------------------------------------------------------- output


def test_the_report_path_is_fixed_and_absolute():
    """The agent is told where to put its report, not asked to choose."""
    output = _manifest(Selection())["output"]
    assert output["report"].endswith("report.md")
    assert output["directory"].endswith("841552464")
    assert Path(output["report"]).is_absolute()


def test_the_manifest_and_the_report_sit_together(tmp_path):
    incident = _incident()
    config = _Config(tmp_path)
    manifest = build_manifest(
        incident, Selection(), config, window=resolve_window(incident, now=NOW)
    )
    assert Path(manifest["output"]["report"]).parent == manifest_path(incident, config).parent


# ------------------------------------------------------------------- capture


def test_the_kits_caveat_travels_with_the_query():
    """It is the fleet's own warning about misreading this query."""
    source = _manifest(Selection(queries=[_query()]))["access"]["queries"][0]
    assert "empty error signal" in source["caveat"]


def test_no_caveat_is_null_not_invented():
    source = _manifest(Selection(queries=[_query(kql="T | take 1")]))["access"]["queries"][0]
    assert source["caveat"] is None


def test_a_redacted_tsg_is_null_but_the_raw_value_is_kept():
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


# ----------------------------------------------------------------- base rate

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


def test_measures_keep_their_denominator():
    """"37.5%" without "(3 of 8)" hides how small the sample is."""
    assert "(3 of 8)" in parse_base_rate(_CARD)["closedByAutomation"]


def test_an_absent_card_yields_no_base_rate():
    assert parse_base_rate("") == {}
    source = _manifest(Selection(queries=[_query()]))["access"]["queries"][0]
    assert source["baseRate"] is None


def test_extract_caveat_reads_only_the_marked_comment():
    assert extract_caveat("// CAVEAT: mind the gap\nT | take 1") == "mind the gap"
    assert extract_caveat("// just a comment\nT | take 1") is None
    assert extract_caveat("") is None


def test_the_path_is_stable(tmp_path):
    incident = _incident()
    config = _Config(tmp_path)
    assert manifest_path(incident, config) == manifest_path(incident, config)
    assert manifest_path(incident, config).name == "payload.json"
