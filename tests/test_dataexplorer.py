"""Deep links into Azure Data Explorer.

A wrong link is worse than no link: it looks like it worked, and sends an
operator to a cluster that does not hold the data they are reasoning about.
"""

from __future__ import annotations

import base64
import gzip
from pathlib import Path
from urllib.parse import unquote

import pytest

from oce_sentry.dataexplorer import (
    MAX_URL,
    build_url,
    kit_target,
    kit_url,
)

_RUN_PS1 = """
$ErrorActionPreference = 'Stop'
$clusterUri = 'https://odspmicroservices.eastus2.kusto.windows.net'
$database   = 'odspmediaprod'
$icmCluster = 'https://icmcluster.kusto.windows.net'
"""

_KQL = """// Investigation kit: something
// Cluster https://stale.kusto.windows.net / staledb
PlatformEvent
| where Timestamp > ago(1d)
| take 10
"""


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    (tmp_path / "run.ps1").write_text(_RUN_PS1, encoding="utf-8")
    (tmp_path / "investigate.kql").write_text(_KQL, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------- target


def test_the_runner_wins_over_the_comment(kit):
    """The header comment can drift from the code it describes.

    Trusting it would send an operator to a cluster the kit never queries,
    with nothing on screen to suggest anything was wrong.
    """
    cluster, database = kit_target(kit)
    assert cluster == "https://odspmicroservices.eastus2.kusto.windows.net"
    assert database == "odspmediaprod"


def test_the_comment_is_used_when_there_is_no_runner(tmp_path):
    (tmp_path / "investigate.kql").write_text(_KQL, encoding="utf-8")
    assert kit_target(tmp_path) == ("https://stale.kusto.windows.net", "staledb")


def test_no_target_when_nothing_records_one(tmp_path):
    (tmp_path / "investigate.kql").write_text("PlatformEvent | take 1", encoding="utf-8")
    assert kit_target(tmp_path) == ("", "")


def test_a_missing_directory_is_not_fatal():
    assert kit_target(None) == ("", "")
    assert kit_url(None) == ""


# ---------------------------------------------------------------------- url


def test_the_url_carries_the_query(kit):
    url = kit_url(kit)
    assert url.startswith("https://dataexplorer.azure.com/clusters/")
    assert "odspmicroservices.eastus2.kusto.windows.net" in url
    assert "/databases/odspmediaprod?query=" in url

    packed = unquote(url.split("query=", 1)[1])
    restored = gzip.decompress(base64.b64decode(packed)).decode("utf-8")
    assert "PlatformEvent" in restored
    assert restored == _KQL


def test_the_scheme_is_stripped_from_the_host():
    url = build_url("https://c.kusto.windows.net", "db", "print 1")
    assert "/clusters/c.kusto.windows.net/" in url
    assert "clusters/https" not in url


def test_no_url_without_a_complete_target():
    assert build_url("", "db", "print 1") == ""
    assert build_url("https://c.kusto.windows.net", "", "print 1") == ""
    assert build_url("https://c.kusto.windows.net", "db", "   ") == ""


def test_an_absurdly_long_query_produces_no_link():
    """Better no link than one the browser truncates into a broken query.

    Uses varied text rather than a repeated character: the query is gzipped
    before it goes in the URL, so two million identical bytes compress to
    almost nothing and would not exercise the limit at all.
    """
    import random

    random.seed(0)
    noise = " ".join(f"col{random.random()}" for _ in range(200_000))
    assert build_url("https://c.kusto.windows.net", "db", noise) == ""


def test_a_normal_kit_stays_well_under_the_limit(kit):
    assert 0 < len(kit_url(kit)) < MAX_URL


# ------------------------------------------------------------------- safety


def test_placeholders_are_not_substituted(tmp_path):
    """The console does not know the window the kit's runner computes.

    Filling one in would produce a query that looks authoritative and measures
    a different period.
    """
    (tmp_path / "run.ps1").write_text(_RUN_PS1, encoding="utf-8")
    (tmp_path / "investigate.kql").write_text(
        "PlatformEvent | where Timestamp between (INCIDENT_START .. INCIDENT_END)",
        encoding="utf-8",
    )
    url = kit_url(tmp_path, incident_id="850000001")
    restored = gzip.decompress(base64.b64decode(unquote(url.split("query=", 1)[1]))).decode()
    assert "INCIDENT_START" in restored
    assert "850000001" not in restored
