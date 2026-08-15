"""Kusto data planes: the clusters behind the connectors.

The MCP inventory lists one row called `azure` that means "Kusto queries", which
is true and nearly useless -- behind that single server sit a dozen distinct
clusters with different owners, different access, and different reasons to be
denied. An operator whose skill just failed needs to know *which* cluster it
could not reach.

Two kinds of plane are listed together because both answer the same question:

*Sentry's own* -- the incident queue and the SLI view query Kusto directly, not
through MCP. If those are denied the console itself is broken.

*The skills'* -- reached through the `azure` MCP server when connectors are
wired. If those are denied, a skill degrades to summarising the evidence pack,
which is what it will do quietly rather than announce.

The registry is transcribed from the RCA agent's own reference
(`.github/references/MCP_Servers_Kusto_Cluster_References.md`) so the purposes
are the team's words, not a guess. Anything a skill names that is not in the
registry is still listed -- an unknown cluster appearing is exactly the signal
that a new data source arrived.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

#: Clusters whose hostnames are deliberately redacted in the reference. They
#: are real, but these are not resolvable names, so probing them would report
#: a failure that says nothing about the operator's access.
REDACTED = ("-redacted.",)

#: cluster host -> (database, purpose). Transcribed from the RCA reference.
REGISTRY: dict[str, tuple[str, str]] = {
    "icmcluster.kusto.windows.net": (
        "IcmDataWarehouse",
        "Incident details, component health, custom fields",
    ),
    "icmclustereu-redacted.westeurope.kusto.windows.net": (
        "IcMDataWarehouse",
        "EU incident warehouse - presence check for the isEU flag",
    ),
    "icmcluster-eu-redacted.westeurope.kusto.windows.net": (
        "IcMDataWarehouse",
        "EU incident warehouse (fallback cluster)",
    ),
    "spogdskustocluster.eastus2.kusto.windows.net": (
        "spoprod",
        "SPO request usage, error patterns, SQL metrics",
    ),
    "spoemeakustocluster.northeurope.kusto.windows.net": (
        "spoprodemea",
        "SPO telemetry for EMEA production farms",
    ),
    "azureprofilerfollower.westus2.kusto.windows.net": (
        "azureprofiler",
        "Profiler traces for USR CPU, memory and request pile-up",
    ),
    "fcmdataro.kusto.windows.net": (
        "FCMKustoStore",
        "Deployments, feature flights, ECS config changes",
    ),
    "azphynet.kusto.windows.net": (
        "NetworkMetadata",
        "Physical network device topology and health",
    ),
    "sqladhoc.kusto.windows.net": (
        "sqlazure1",
        "Resolves the regional Azure SQL diagnostics cluster",
    ),
    "apim.kusto.windows.net": (
        "APIMProd",
        "APIM proxy diagnostics for correlation investigations",
    ),
    "processus.kusto.windows.net": (
        "process",
        "Power Platform / Flow telemetry (US)",
    ),
    "processeurope.kusto.windows.net": (
        "process",
        "Power Platform / Flow telemetry (Europe)",
    ),
    "genevaslidatafollower.westcentralus.kusto.windows.net": (
        "slidata",
        "Geneva SLI data - the service level indicators view",
    ),
}

#: Hosts that are a region suffix rather than a cluster, picked up by a bare
#: hostname match in prose. Listing them as clusters would be wrong.
_NOT_CLUSTERS = re.compile(
    r"^(eastus2|northeurope|westeurope|westus2|westcentralus)\.kusto\.windows\.net$",
    re.I,
)

_CLUSTER_IN_TEXT = re.compile(r"\b([a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.kusto\.windows\.net)\b", re.I)


@dataclass
class DataPlane:
    host: str
    database: str = ""
    purpose: str = ""
    #: sentry | skills | both
    used_by: str = "skills"
    #: declared | ready | denied | unreachable | redacted
    status: str = "declared"
    detail: str = ""
    #: Skills naming this cluster.
    required_by: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://{self.host}"

    @property
    def redacted(self) -> bool:
        return any(marker in self.host for marker in REDACTED)

    @property
    def label(self) -> str:
        return f"{self.host}/{self.database}" if self.database else self.host


def _sentry_planes(config) -> dict[str, DataPlane]:
    """The two planes the console queries itself, without MCP."""
    planes: dict[str, DataPlane] = {}

    icm = getattr(config.policy, "icm", None) or {}
    cluster = str(icm.get("cluster", "")).replace("https://", "").rstrip("/")
    if cluster:
        planes[cluster] = DataPlane(
            host=cluster,
            database=str(icm.get("database", "")),
            purpose="The incident queue - this console's primary source",
            used_by="sentry",
        )

    sli_cluster = os.environ.get(
        "OCE_SENTRY_SLI_CLUSTER",
        "https://genevaslidatafollower.westcentralus.kusto.windows.net",
    )
    host = sli_cluster.replace("https://", "").rstrip("/")
    planes[host] = DataPlane(
        host=host,
        database=os.environ.get("OCE_SENTRY_SLI_DATABASE", "slidata"),
        purpose="Geneva SLI data - the service level indicators view",
        used_by="sentry",
    )
    return planes


def discover_planes(config, skills) -> list[DataPlane]:
    """Everything Sentry or an installed skill queries, in one list."""
    planes = _sentry_planes(config)

    for skill in skills:
        body = f"{skill.description}\n{skill.body}"
        for host in {h.lower() for h in _CLUSTER_IN_TEXT.findall(body)}:
            if _NOT_CLUSTERS.match(host):
                continue
            plane = planes.get(host)
            if plane is None:
                database, purpose = REGISTRY.get(host, ("", ""))
                plane = DataPlane(
                    host=host,
                    database=database,
                    purpose=purpose or "Not in the reference - newly named by a skill",
                    used_by="skills",
                )
                planes[host] = plane
            elif plane.used_by == "sentry":
                plane.used_by = "both"
            plane.required_by.append(skill.id)

    for host, plane in planes.items():
        if not plane.purpose and host in REGISTRY:
            plane.database, plane.purpose = REGISTRY[host]
        if plane.redacted:
            plane.status = "redacted"
            plane.detail = "hostname is redacted in the reference; cannot be probed"

    # Sentry's own first: if those are denied the console itself is broken.
    order = {"sentry": 0, "both": 1, "skills": 2}
    return sorted(
        planes.values(),
        key=lambda p: (order.get(p.used_by, 9), -len(p.required_by), p.host),
    )


def probe_plane(plane: DataPlane, tokens, timeout: int = 30) -> DataPlane:
    """Ask the cluster a trivial question, as the operator.

    `print` is evaluated in the database's context, so a success proves network,
    token and database authorisation together -- which is the whole question.
    Nothing is read: this must never be a way to sample production data.
    """
    if plane.redacted:
        plane.status = "redacted"
        return plane

    from .kusto import KustoClient, KustoError

    try:
        client = KustoClient(tokens, timeout=timeout)
        client.query(
            cluster=plane.url,
            database=plane.database or "",
            query="print ProbeOk=1",
        )
        plane.status = "ready"
        plane.detail = "query accepted"
    except KustoError as exc:
        message = str(exc)
        lowered = message.lower()
        if "not authoris" in lowered or "not author" in lowered or "403" in message:
            plane.status = "denied"
        elif "could not reach" in lowered or "timed out" in lowered:
            plane.status = "unreachable"
        else:
            plane.status = "denied"
        plane.detail = message.split(".")[0][:80]
    except Exception as exc:  # noqa: BLE001 - auth failures surface here
        plane.status = "unreachable"
        plane.detail = f"{type(exc).__name__}: {exc}"[:80]
    return plane


def plane_summary(planes: list[DataPlane]) -> str:
    if not planes:
        return "no data planes found"
    probed = [p for p in planes if p.status in ("ready", "denied", "unreachable")]
    if not probed:
        return f"{len(planes)} cluster(s) declared, none probed (p)"
    ready = sum(1 for p in probed if p.status == "ready")
    return f"{ready} of {len(probed)} probed cluster(s) reachable"
