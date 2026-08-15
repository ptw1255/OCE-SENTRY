"""Kusto data planes: the clusters behind the connectors."""

from __future__ import annotations

from pathlib import Path

import pytest

from oce_sentry.dataplanes import (
    ACCESS,
    BASELINE_ACCESS,
    REGISTRY,
    Access,
    DataPlane,
    access_for,
    discover_planes,
    load_access,
    load_registry,
    parse_access,
    parse_registry,
    plane_summary,
)
from oce_sentry.skills import Skill


class _Policy:
    icm = {"cluster": "https://icmcluster.kusto.windows.net", "database": "IcmDataWarehouse"}


class _Config:
    policy = _Policy()


def _skill(skill_id: str, body: str) -> Skill:
    return Skill(
        id=skill_id, name=skill_id, description="", body=body,
        source="ado", directory=Path("."),
    )


# ------------------------------------------------------------------ discovery


def test_sentrys_own_planes_are_always_listed():
    """The queue and the SLI view query Kusto directly, not through MCP."""
    planes = {p.host: p for p in discover_planes(_Config(), [])}
    assert "icmcluster.kusto.windows.net" in planes
    assert any("genevaslidatafollower" in host for host in planes)
    assert all(p.used_by == "sentry" for p in planes.values())


def test_sentrys_plane_named_by_a_skill_is_marked_as_both():
    planes = {
        p.host: p
        for p in discover_planes(
            _Config(), [_skill("icm", "Query https://icmcluster.kusto.windows.net")]
        )
    }
    icm = planes["icmcluster.kusto.windows.net"]
    assert icm.used_by == "both"
    assert icm.required_by == ["icm"]


def test_sentrys_planes_sort_first():
    """If these are denied the console itself is broken, not just a skill."""
    planes = discover_planes(
        _Config(), [_skill("s", "https://fcmdataro.kusto.windows.net")]
    )
    assert planes[0].used_by in ("sentry", "both")


def test_a_cluster_only_a_skill_names_is_still_listed():
    planes = {p.host: p for p in discover_planes(_Config(), [_skill("fcm", "fcmdataro.kusto.windows.net")])}
    fcm = planes["fcmdataro.kusto.windows.net"]
    assert fcm.used_by == "skills"
    assert fcm.database == "FCMKustoStore"
    assert "Deployments" in fcm.purpose


def test_an_unknown_cluster_is_surfaced_rather_than_dropped():
    """A cluster appearing that the reference does not know is the signal
    that a new data source arrived, which is the whole point of tracking."""
    planes = {p.host: p for p in discover_planes(_Config(), [_skill("new", "brandnew.kusto.windows.net")])}
    new = planes["brandnew.kusto.windows.net"]
    assert "Not in the reference" in new.purpose


def test_bare_region_hostnames_are_not_mistaken_for_clusters():
    """`eastus2.kusto.windows.net` is a suffix in prose, not a cluster."""
    planes = {p.host for p in discover_planes(_Config(), [_skill("s", "eastus2.kusto.windows.net")])}
    assert "eastus2.kusto.windows.net" not in planes


def test_the_same_cluster_named_twice_is_one_row():
    skill = _skill("s", "fcmdataro.kusto.windows.net and again fcmdataro.kusto.windows.net")
    planes = [p for p in discover_planes(_Config(), [skill]) if "fcmdataro" in p.host]
    assert len(planes) == 1
    assert planes[0].required_by == ["s"]


def test_skill_counts_drive_the_order_within_a_group():
    skills = [
        _skill("a", "fcmdataro.kusto.windows.net"),
        _skill("b", "fcmdataro.kusto.windows.net"),
        _skill("c", "apim.kusto.windows.net"),
    ]
    planes = [p for p in discover_planes(_Config(), skills) if p.used_by == "skills"]
    assert planes[0].host.startswith("fcmdataro")


# ------------------------------------------------------------------ redaction


def test_redacted_hosts_are_listed_but_not_probeable():
    """The reference redacts these hostnames deliberately.

    Probing them would report a DNS failure that says nothing about the
    operator's access, so they are marked rather than tested.
    """
    skill = _skill("scrub", "https://icmclustereu-redacted.westeurope.kusto.windows.net")
    plane = next(p for p in discover_planes(_Config(), [skill]) if p.redacted)
    assert plane.status == "redacted"
    assert "redacted" in plane.detail


def test_a_normal_host_is_not_treated_as_redacted():
    plane = DataPlane(host="fcmdataro.kusto.windows.net")
    assert not plane.redacted


# -------------------------------------------------------------------- summary


def test_summary_says_when_nothing_has_been_probed():
    planes = discover_planes(_Config(), [])
    assert "none probed" in plane_summary(planes)


def test_summary_counts_only_probed_planes():
    planes = [
        DataPlane(host="a", status="ready"),
        DataPlane(host="b", status="denied"),
        DataPlane(host="c", status="declared"),
        DataPlane(host="d", status="redacted"),
    ]
    assert plane_summary(planes) == "1 of 2 probed cluster(s) reachable"


def test_summary_handles_an_empty_inventory():
    assert plane_summary([]) == "no data planes found"


# ------------------------------------------------------------------- registry


def test_registry_entries_all_carry_a_database_and_a_purpose():
    for host, (database, purpose) in REGISTRY.items():
        assert database, host
        assert purpose, host


def test_registry_covers_the_clusters_sentry_itself_uses():
    assert "icmcluster.kusto.windows.net" in REGISTRY
    assert "genevaslidatafollower.westcentralus.kusto.windows.net" in REGISTRY


# --------------------------------------------------------------------- access


def test_documented_clusters_carry_a_requirement_and_a_request_link():
    """A denied cluster is useless information without the thing to request."""
    for host in ACCESS:
        access = access_for(host)
        assert access.documented, host
        assert access.request_url.startswith("https://"), host
        assert access.source, host


def test_access_is_attached_when_planes_are_discovered():
    planes = {p.host: p for p in discover_planes(_Config(), [])}
    icm = planes["icmcluster.kusto.windows.net"]
    assert icm.access.requirement == "IcM-Kusto-Access entitlement"
    assert "coreidentity.microsoft.com" in icm.access.request_url


def test_an_undocumented_cluster_says_so_rather_than_guessing():
    """Inventing an entitlement sends an operator to the wrong approver."""
    planes = {p.host: p for p in discover_planes(_Config(), [_skill("s", "apim.kusto.windows.net")])}
    apim = planes["apim.kusto.windows.net"]
    assert not apim.access.documented
    assert apim.access.short == "-"


def test_access_short_form_fits_a_table_cell():
    access = access_for("spoemeakustocluster.northeurope.kusto.windows.net")
    assert access.short == "Corp-ODSP-ReadAccess_User"
    assert len(access.short) <= 34


def test_both_spo_clusters_point_at_the_same_request():
    """The onboarding guide says one request covers both; saying otherwise
    would have an operator file a duplicate."""
    a = access_for("spogdskustocluster.eastus2.kusto.windows.net")
    b = access_for("spoemeakustocluster.northeurope.kusto.windows.net")
    assert a.request_url == b.request_url


def test_the_baseline_names_az_login():
    assert "az login" in BASELINE_ACCESS


# ------------------------------------------------------- reading the team's docs


_ONBOARDING_TABLE = """
## Required Access

| Resource | Access required | Request |
|---|---|---|
| `https://azphynet.kusto.windows.net/` | IDWeb group `AznwKustoReader` | [Request access](https://idweb.example/aznw) |
| `https://icmcluster.kusto.windows.net/` | `IcM-Kusto-Access` entitlement | [Request access](https://coreidentity.example/icm) |
| Some unrelated row | no host here | [link](https://example.com) |
"""

_CLUSTER_DOC = """
### 1. ICM Cluster

| Property | Value |
|----------|-------|
| **Cluster URI** | `https://icmcluster.kusto.windows.net/` |
| **Database** | `IcmDataWarehouse` |
| **Purpose** | Incident details, component health |

### 4. azphynet Cluster

| Property | Value |
|----------|-------|
| **Cluster URI** | `https://azphynet.kusto.windows.net/` |
| **Databases** | `NetworkMetadata` (topology), `azdhbackupmds` (device health) |
| **Purpose** | Physical network device topology |

### 5. Regional, resolved at runtime

| Property | Value |
|----------|-------|
| **Database** | `SQLAzure1` |
| **Purpose** | Azure SQL platform telemetry |
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "ONBOARDING.md").write_text(_ONBOARDING_TABLE, encoding="utf-8")
    reference = tmp_path / ".github" / "references"
    reference.mkdir(parents=True)
    (reference / "MCP_Servers_Kusto_Cluster_References.md").write_text(
        _CLUSTER_DOC, encoding="utf-8"
    )
    return tmp_path


def test_access_is_read_from_the_onboarding_guide(repo):
    parsed = parse_access(repo / "ONBOARDING.md")
    assert parsed["icmcluster.kusto.windows.net"][0] == "IcM-Kusto-Access entitlement"
    assert parsed["icmcluster.kusto.windows.net"][1] == "https://coreidentity.example/icm"


def test_rows_without_a_cluster_host_are_skipped(repo):
    parsed = parse_access(repo / "ONBOARDING.md")
    assert len(parsed) == 2


def test_the_document_wins_over_the_snapshot(repo):
    """Sentry does not own these facts; a renamed entitlement must not persist."""
    live = load_access(repo)
    assert live["icmcluster.kusto.windows.net"][1] == "https://coreidentity.example/icm"
    assert live["icmcluster.kusto.windows.net"][2] == "ONBOARDING.md"


def test_snapshot_entries_are_labelled_as_snapshots(repo):
    """The SLI cluster is not in the onboarding table, so it stays built-in."""
    live = load_access(repo)
    _, _, source = live["genevaslidatafollower.westcentralus.kusto.windows.net"]
    assert source.startswith("snapshot")
    assert not Access(source=source).live
    assert Access(source="ONBOARDING.md").live


def test_registry_is_read_from_the_cluster_reference(repo):
    parsed = parse_registry(repo / ".github" / "references" / "MCP_Servers_Kusto_Cluster_References.md")
    assert parsed["icmcluster.kusto.windows.net"] == (
        "IcmDataWarehouse",
        "Incident details, component health",
    )


def test_multi_database_rows_take_the_first_without_its_gloss(repo):
    parsed = parse_registry(repo / ".github" / "references" / "MCP_Servers_Kusto_Cluster_References.md")
    assert parsed["azphynet.kusto.windows.net"][0] == "NetworkMetadata"


def test_sections_without_a_cluster_uri_are_skipped(repo):
    """The regional SQL section describes a lookup, not a fixed cluster."""
    parsed = parse_registry(repo / ".github" / "references" / "MCP_Servers_Kusto_Cluster_References.md")
    assert len(parsed) == 2


def test_a_missing_checkout_falls_back_to_the_snapshot(tmp_path):
    """`--connectors` must still say something useful without the repo."""
    live = load_access(tmp_path)
    assert live["icmcluster.kusto.windows.net"][0] == "IcM-Kusto-Access entitlement"
    assert live["icmcluster.kusto.windows.net"][2].startswith("snapshot")
    assert load_registry(tmp_path)["icmcluster.kusto.windows.net"][0] == "IcmDataWarehouse"


def test_a_malformed_document_falls_back_rather_than_emptying(tmp_path):
    (tmp_path / "ONBOARDING.md").write_text("no tables here", encoding="utf-8")
    assert load_access(tmp_path) == _snapshot_labelled()


def _snapshot_labelled() -> dict:
    from oce_sentry.dataplanes import _as_snapshot

    return _as_snapshot(ACCESS)


# ---------------------------------------------------------------- consequence


def test_consequence_distinguishes_the_console_from_a_skill():
    """The reason to go and request access differs by what breaks."""
    planes = {p.host: p for p in discover_planes(_Config(), [])}
    icm = planes["icmcluster.kusto.windows.net"]
    assert icm.consequence == "The incident queue cannot load."
    sli = next(p for p in planes.values() if "slidata" in p.database)
    assert sli.consequence == "The SLI view cannot load."


def test_consequence_counts_the_skills_that_would_degrade():
    plane = DataPlane(host="h", used_by="skills", required_by=["a", "b"])
    assert plane.consequence == "2 skill(s) fall back to the evidence pack."


def test_consequence_is_honest_when_nothing_depends_on_it():
    assert DataPlane(host="h", used_by="skills").consequence == "No installed skill depends on it."
