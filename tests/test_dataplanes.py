"""Kusto data planes: the clusters behind the connectors."""

from __future__ import annotations

from pathlib import Path

import pytest

from oce_sentry.dataplanes import (
    ACCESS,
    BASELINE_ACCESS,
    REGISTRY,
    DataPlane,
    access_for,
    discover_planes,
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
