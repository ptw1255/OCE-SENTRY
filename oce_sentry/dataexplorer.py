"""Deep links into Azure Data Explorer.

A query result is a table, and a terminal is the wrong place to read a wide
one: 150 columns of monitor breakdown in a 116-column pane means scrolling in
two dimensions to read a single row. The operator already has a browser signed
in to the same tenant, and ADX gives them sorting, filtering, charting and
export for free.

So the console renders the result it already has -- that output is evidence
and feeds later skill runs -- and offers a link to the same query in the tool
built for reading it.

The link carries the query itself, gzipped and base64'd, which is the format
the ADX web UI expects. Nothing is executed here and no credentials are
involved: the browser authenticates as the operator, exactly as it would if
they had pasted the query themselves.
"""

from __future__ import annotations

import base64
import gzip
import re
from pathlib import Path
from urllib.parse import quote

ADX = "https://dataexplorer.azure.com"

#: Kits record their target in a header comment:
#:     // Cluster https://odspmicroservices.eastus2.kusto.windows.net / odspmediaprod
_TARGET_IN_KQL = re.compile(
    r"^//\s*Cluster\s+(https?://[^\s/]+)\s*/\s*(\S+)", re.M | re.I
)
#: run.ps1 assigns them, which is the authoritative pair the kit actually uses.
_CLUSTER_IN_PS1 = re.compile(r"\$clusterUri\s*=\s*'([^']+)'")
_DATABASE_IN_PS1 = re.compile(r"\$database\s*=\s*'([^']+)'")

#: Modern browsers accept far more, but a link this long is a sign the query
#: is not something a human wants pasted into a URL bar either.
MAX_URL = 8000


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def kit_target(directory: Path | None) -> tuple[str, str]:
    """The cluster and database a kit queries.

    `run.ps1` wins over the KQL header: the header is a comment, and a comment
    that has drifted from the code would send an operator to the wrong cluster
    with no sign anything was wrong.
    """
    if directory is None:
        return "", ""

    script = _read(directory / "run.ps1")
    cluster = _CLUSTER_IN_PS1.search(script)
    database = _DATABASE_IN_PS1.search(script)
    if cluster and database:
        return cluster.group(1), database.group(1)

    match = _TARGET_IN_KQL.search(_read(directory / "investigate.kql"))
    if match:
        return match.group(1), match.group(2)
    return "", ""


def kit_query(directory: Path | None, incident_id: str = "") -> str:
    """The kit's KQL, with the incident window placeholders left in place.

    The placeholders are deliberately not substituted. The console does not
    know the window the kit's own runner computes, and quietly filling in a
    guessed range would produce a query that looks authoritative and measures
    something else.
    """
    if directory is None:
        return ""
    return _read(directory / "investigate.kql")


def build_url(cluster: str, database: str, query: str) -> str:
    """A deep link that opens the query in ADX, ready to run."""
    if not cluster or not database or not query.strip():
        return ""
    host = cluster.replace("https://", "").replace("http://", "").strip("/")
    packed = quote(base64.b64encode(gzip.compress(query.encode("utf-8"))).decode("ascii"))
    url = f"{ADX}/clusters/{host}/databases/{database}?query={packed}"
    return url if len(url) <= MAX_URL else ""


def kit_url(directory: Path | None, incident_id: str = "") -> str:
    """The ADX link for a kit, or empty if its target cannot be determined.

    Empty rather than a guess: a link to the wrong cluster is worse than no
    link, because it looks like it worked.
    """
    cluster, database = kit_target(directory)
    return build_url(cluster, database, kit_query(directory, incident_id))
