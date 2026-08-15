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
from pathlib import Path

#: Clusters whose hostnames are deliberately redacted in the reference. They
#: are real, but these are not resolvable names, so probing them would report
#: a failure that says nothing about the operator's access.
REDACTED = ("-redacted.",)

#: cluster host -> (database, purpose).
#:
#: A FALLBACK ONLY. The live values are parsed from the RCA agent's own
#: `MCP_Servers_Kusto_Cluster_References.md` at runtime -- see `load_registry`.
#: This copy exists so a machine without that checkout still gets a useful
#: answer, and it will drift, because Sentry does not own these facts.
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

#: cluster host -> what an operator must hold to query it, and where to ask.
#:
#: A FALLBACK ONLY. The live values are parsed from the RCA agent's
#: ONBOARDING.md at runtime -- see `load_access`. An entitlement that has been
#: renamed and is still shown from here would send an on-call engineer to an
#: approver who will reject them, which is why the checkout always wins.
ACCESS: dict[str, tuple[str, str, str]] = {
    "icmcluster.kusto.windows.net": (
        "IcM-Kusto-Access entitlement",
        "https://coreidentity.microsoft.com/manage/Entitlement/entitlement/icmkustoacce-ufk0",
        "ONBOARDING.md",
    ),
    "icmclustereu-redacted.westeurope.kusto.windows.net": (
        "IcM-Kusto-Access entitlement, plus EU access from the owning team",
        "https://coreidentity.microsoft.com/manage/Entitlement/entitlement/icmkustoacce-ufk0",
        "ONBOARDING.md + scrub-cri2lsi",
    ),
    "icmcluster-eu-redacted.westeurope.kusto.windows.net": (
        "IcM-Kusto-Access entitlement, plus EU access from the owning team",
        "https://coreidentity.microsoft.com/manage/Entitlement/entitlement/icmkustoacce-ufk0",
        "ONBOARDING.md + scrub-cri2lsi",
    ),
    "spogdskustocluster.eastus2.kusto.windows.net": (
        "Corp-ODSP-ReadAccess_User (M365 Pulse)",
        "https://m365pulse.microsoft.com/v2/CorpIdentity/RequestAccess/RequestScenario"
        "?scenarioName=Corp-ODSP-ReadAccess_User",
        "ONBOARDING.md",
    ),
    "spoemeakustocluster.northeurope.kusto.windows.net": (
        "Corp-ODSP-ReadAccess_User (the same request covers both SPO clusters)",
        "https://m365pulse.microsoft.com/v2/CorpIdentity/RequestAccess/RequestScenario"
        "?scenarioName=Corp-ODSP-ReadAccess_User",
        "ONBOARDING.md",
    ),
    "fcmdataro.kusto.windows.net": (
        "IDWeb group fcmusers",
        "https://idweb.microsoft.com/IdentityManagement/aspx/common/GlobalSearchResult.aspx"
        "?searchtype=e0c132db-08d8-4258-8bce-561687a8a51e&content=fcmusers",
        "ONBOARDING.md",
    ),
    "azphynet.kusto.windows.net": (
        "IDWeb group AznwKustoReader",
        "https://idweb.microsoft.com/IdentityManagement/aspx/common/GlobalSearchResult.aspx"
        "?searchtype=e0c132db-08d8-4258-8bce-561687a8a51e&content=AznwKustoReader",
        "ONBOARDING.md",
    ),
    "genevaslidatafollower.westcentralus.kusto.windows.net": (
        "Geneva read access; monitor writes additionally need MonitorEditor",
        "https://eng.ms/docs/products/geneva/alerts/howdoi/viewmonitorconfigsnapshothistory",
        "livesite-management-hygiene preflight",
    ),
}

#: Everything needs this first, whatever else it needs.
BASELINE_ACCESS = "az login (Azure CLI) - every cluster authenticates through it"

#: Where the RCA agent repository usually is. Its documents are the source of
#: truth for both the cluster registry and the access requirements; the
#: dictionaries above are a snapshot for when it is not on this machine.
_REPO_CANDIDATES = (
    Path.home() / "repos" / "SRELivesite-RCAAgent",
    Path.home() / "source" / "repos" / "SRELivesite-RCAAgent",
)

_ONBOARDING = "ONBOARDING.md"
_CLUSTER_REFERENCE = Path(".github") / "references" / "MCP_Servers_Kusto_Cluster_References.md"

_HOST_IN_TEXT = re.compile(r"([a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.kusto\.windows\.net)", re.I)
_MD_LINK = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")


def reference_repo() -> Path | None:
    """The RCA agent checkout, if this machine has one."""
    override = os.environ.get("OCE_SENTRY_RCA_REPO")
    if override:
        path = Path(override).expanduser()
        return path if path.is_dir() else None
    return next((p for p in _REPO_CANDIDATES if p.is_dir()), None)


def _clean(cell: str) -> str:
    return cell.replace("`", "").replace("**", "").strip()


def parse_access(path: Path) -> dict[str, tuple[str, str, str]]:
    """Read the onboarding guide's Required Access table.

    Rows look like:

        | `https://fcmdataro.kusto.windows.net/` | IDWeb group `fcmusers` | [Request access](url) |

    Only rows naming a Kusto host and carrying a link are taken. A row that
    parses to a requirement without a request link is dropped rather than
    shown, because a requirement an operator cannot act on is not much better
    than silence.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    found: dict[str, tuple[str, str, str]] = {}
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c for c in line.split("|")]
        if len(cells) < 4:
            continue
        host_match = _HOST_IN_TEXT.search(cells[1])
        link_match = _MD_LINK.search(cells[3])
        requirement = _clean(cells[2])
        if not host_match or not link_match or not requirement:
            continue
        found[host_match.group(1).lower()] = (
            requirement,
            link_match.group(1),
            path.name,
        )
    return found


def parse_registry(path: Path) -> dict[str, tuple[str, str]]:
    """Read the cluster reference's property tables.

    Each cluster is a `### N. Name` section followed by a two-column table of
    Cluster URI / Database(s) / Purpose. Sections without a cluster URI -- the
    regional SQL section describes a lookup rather than a fixed cluster -- are
    skipped rather than guessed at.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    found: dict[str, tuple[str, str]] = {}
    host = database = purpose = ""

    def flush() -> None:
        nonlocal host, database, purpose
        if host:
            found[host] = (database, purpose)
        host = database = purpose = ""

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            continue
        if not line.strip().startswith("|"):
            continue
        cells = [c for c in line.split("|")]
        if len(cells) < 3:
            continue
        key, value = _clean(cells[1]).lower(), _clean(cells[2])
        if key.startswith("cluster uri"):
            match = _HOST_IN_TEXT.search(value)
            if match:
                host = match.group(1).lower()
        elif key.startswith("database"):
            # "NetworkMetadata (topology), azdhbackupmds (device health)" ->
            # the first database, without the gloss.
            first = value.split(",")[0]
            database = re.sub(r"\s*\(.*?\)\s*", "", first).strip()
        elif key.startswith("purpose"):
            purpose = value
    flush()
    return found


def _as_snapshot(table: dict[str, tuple[str, str, str]]) -> dict[str, tuple[str, str, str]]:
    """Label built-in values as what they are.

    An operator acting on an entitlement name deserves to know whether it came
    from the team's current document or from a copy that may be months stale.
    """
    return {
        host: (requirement, url, source if source.startswith("snapshot") else f"snapshot of {source}")
        for host, (requirement, url, source) in table.items()
    }


def load_access(repo: Path | None = None) -> dict[str, tuple[str, str, str]]:
    """Access requirements, preferring the team's document over the snapshot.

    Sentry does not own these facts. An entitlement that has been renamed and
    is still shown here sends an on-call engineer to an approver who will
    reject them, so the checkout wins whenever there is one -- and the built-in
    copy exists only so `--connectors` still says something useful on a machine
    without the repository.
    """
    repo = repo or reference_repo()
    merged = _as_snapshot(ACCESS)
    if repo is not None:
        parsed = parse_access(repo / _ONBOARDING)
        if parsed:
            merged.update(parsed)
    return merged


def load_registry(repo: Path | None = None) -> dict[str, tuple[str, str]]:
    repo = repo or reference_repo()
    if repo is not None:
        parsed = parse_registry(repo / _CLUSTER_REFERENCE)
        if parsed:
            merged = dict(REGISTRY)
            for host, (database, purpose) in parsed.items():
                fallback_db, fallback_purpose = merged.get(host, ("", ""))
                merged[host] = (database or fallback_db, purpose or fallback_purpose)
            return merged
    return dict(REGISTRY)


@dataclass
class Access:
    requirement: str = ""
    request_url: str = ""
    source: str = ""

    @property
    def documented(self) -> bool:
        return bool(self.requirement)

    @property
    def live(self) -> bool:
        """Read from the team's checkout rather than the built-in snapshot.

        The distinction is worth surfacing: a snapshot value may be months out
        of date, and the operator should know which they are acting on.
        """
        return bool(self.source) and not self.source.startswith("snapshot")

    @property
    def short(self) -> str:
        """A table cell: the group or entitlement name, without the prose."""
        if not self.requirement:
            return "-"
        return self.requirement.split(",")[0].split("(")[0].strip()[:34]


def access_for(host: str, table: dict[str, tuple[str, str, str]] | None = None) -> Access:
    entry = (table if table is not None else ACCESS).get(host)
    if entry is None:
        return Access()
    requirement, url, source = entry
    return Access(requirement=requirement, request_url=url, source=source)


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
    access: Access = field(default_factory=Access)

    @property
    def url(self) -> str:
        return f"https://{self.host}"

    @property
    def redacted(self) -> bool:
        return any(marker in self.host for marker in REDACTED)

    @property
    def label(self) -> str:
        return f"{self.host}/{self.database}" if self.database else self.host

    @property
    def consequence(self) -> str:
        """What an operator loses without this. The reason to go and ask."""
        if self.used_by == "sentry":
            if "slidata" in self.database:
                return "The SLI view cannot load."
            return "The incident queue cannot load."
        if self.used_by == "both":
            return "The incident queue cannot load, and skills lose incident context."
        if self.required_by:
            return f"{len(self.required_by)} skill(s) fall back to the evidence pack."
        return "No installed skill depends on it."


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
    registry = load_registry()
    access_table = load_access()

    for skill in skills:
        body = f"{skill.description}\n{skill.body}"
        for host in {h.lower() for h in _CLUSTER_IN_TEXT.findall(body)}:
            if _NOT_CLUSTERS.match(host):
                continue
            plane = planes.get(host)
            if plane is None:
                database, purpose = registry.get(host, ("", ""))
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
        if not plane.purpose and host in registry:
            plane.database, plane.purpose = registry[host]
        plane.access = access_for(host, access_table)
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
