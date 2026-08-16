"""The payload manifest.

An address book for the operator's agent: what to run, where to run it, how to
invoke it, in the order the operator chose. JSON rather than prose because the
consumer is a program -- it should not have to parse English to find a cluster
URI -- and because a machine-readable shape makes a promise prose cannot keep:
every value is either copied from a source or is a structural label. There is
no advice in here, and nothing that was reasoned about.

Where a fact could not be captured, the field is `null` and a sibling `source`
says where it would have come from. An absent value is information; a plausible
default is a lie an agent cannot detect.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import Incident
from .payload import Selection, resolve_window

SCHEMA = "oce-sentry/payload@1"

#: The base-rate card is a markdown table of measures. Parsing it means the
#: manifest can carry the numbers themselves rather than 120 lines of prose an
#: agent would have to read to find them. The card is still referenced by path,
#: so nothing is lost by not inlining it.
_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")

_MEASURES = {
    "firings in window": "firings",
    "first seen": "firstSeen",
    "last seen": "lastSeen",
    "trend": "trend",
    "closed by automation": "closedByAutomation",
    "customer impacting": "customerImpacting",
    "customer reported": "customerReported",
    "lowest severity paged": "lowestSeverityPaged",
    "open right now": "openNow",
    "median time to mitigate": "medianTimeToMitigate",
    "distinct signatures": "distinctSignatures",
    "distinct farms/regions named": "distinctFarmsOrRegions",
}


def _clean(value: str) -> str:
    return _BOLD.sub(r"\1", value).replace("*", "").strip()


def parse_base_rate(card: str) -> dict[str, Any]:
    """The card's measures, as they are written.

    Values are kept verbatim -- "8 (0.62/week)", "37.5% (3 of 8)" -- rather
    than split into numbers. The parenthetical is part of the measurement, and
    discarding it to get a tidy integer would throw away the denominator.
    """
    if not card.strip():
        return {}
    found: dict[str, Any] = {}
    for line in card.splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        label, value = _clean(match.group(1)).lower(), _clean(match.group(2))
        key = _MEASURES.get(label)
        if key and value:
            found[key] = value
    return found


#: The kit records a caveat in its own KQL as a comment. It is the fleet's
#: analysis of how this query can be misread, and it belongs with the query
#: rather than being stripped as noise.
_CAVEAT = re.compile(r"^//\s*CAVEAT:\s*(.+)$", re.M)


def extract_caveat(kql: str) -> str | None:
    match = _CAVEAT.search(kql or "")
    return match.group(1).strip() if match else None


def _incident_block(incident: Incident, config) -> dict[str, Any]:
    icm = config.policy.icm
    tsg = incident.tsg_id if incident.tsg_id.lower().startswith("http") else None
    return {
        "id": incident.incident_id,
        "title": incident.title,
        "severity": incident.severity,
        "status": incident.status,
        "incidentType": incident.incident_type,
        "environmentClass": incident.env_class,
        "monitorId": incident.monitor_id or None,
        "owningTeam": incident.owning_team_name or None,
        "owningContactAlias": incident.owning_contact_alias or None,
        "trackReason": incident.track_reason,
        "createDate": incident.create_date,
        "mitigateDate": incident.mitigate_date,
        "isTerminal": incident.is_terminal,
        "isCustomerImpacting": incident.is_customer_impacting,
        "isStale": incident.is_stale,
        "hoursOpen": round(incident.hours_open, 1),
        "icmUrl": incident.icm_url,
        # null rather than the literal IcM stores for MSRC incidents, which is
        # "** REDACTED **" and is not a link.
        "tsgUrl": tsg,
        "tsgRaw": incident.tsg_id or None,
        "description": incident.description or None,
        "source": {
            "cluster": icm["cluster"],
            "database": icm["database"],
            "table": icm["tables"]["incidents"],
        },
    }


def _query_step(order: int, item, explorer_url: str | None) -> dict[str, Any]:
    step: dict[str, Any] = {
        "order": order,
        "type": "query",
        "id": item.kit_id,
        "run": {
            "via": "mcp",
            "server": "azure",
            "tool": "kusto_query",
            "arguments": {
                "cluster-uri": item.cluster,
                "database": item.database,
                "query": item.kql,
            },
        },
        "windowSubstituted": True,
        "caveat": extract_caveat(item.kql),
        "evidence": {
            "baseRate": parse_base_rate(item.base_rate_card) or None,
            "cardPath": str(item.directory / "README.md") if item.directory else None,
            "queryPath": str(item.directory / "investigate.kql") if item.directory else None,
        },
    }
    if explorer_url:
        step["alternate"] = {"via": "browser", "url": explorer_url}
    return step


def _skill_step(order: int, item) -> dict[str, Any]:
    return {
        "order": order,
        "type": "skill",
        "id": item.skill_id,
        "run": {
            "via": "file",
            "path": str(item.instruction_path),
        },
        "description": item.description or None,
        "source": {
            "repo": item.source_repo or None,
            "directory": str(item.directory),
        },
    }


def build_manifest(
    incident: Incident,
    selection: Selection,
    config,
    connectors=None,
    window: tuple[str, str, str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """The manifest, as a plain dict.

    Steps are ordered: queries first, then skills, each in the order the
    operator selected them. The order is the operator's sequence, not a
    recommendation -- measure before reasoning is the only opinion here, and it
    is structural rather than analytical.
    """
    from .dataexplorer import build_url

    start, end, derivation = window or resolve_window(incident)

    steps: list[dict[str, Any]] = []
    for item in selection.queries:
        steps.append(
            _query_step(
                len(steps) + 1,
                item,
                build_url(item.cluster, item.database, item.kql),
            )
        )
    for item in selection.skills:
        steps.append(_skill_step(len(steps) + 1, item))

    clusters = []
    for cluster, database in sorted({(q.cluster, q.database) for q in selection.queries}):
        used_by = [s["order"] for s in steps
                   if s["type"] == "query"
                   and s["run"]["arguments"]["cluster-uri"] == cluster
                   and s["run"]["arguments"]["database"] == database]
        clusters.append(
            {"uri": cluster, "database": database, "usedBySteps": used_by, **_access(cluster)}
        )

    return {
        "schema": SCHEMA,
        "generatedAt": generated_at,
        "note": (
            "Every value is copied from IcM or from a file on disk. "
            "Nothing here was inferred or generated."
        ),
        "incident": _incident_block(incident, config),
        "window": {
            "start": start,
            "end": end,
            "derivation": derivation,
            "derivedFrom": ["CreateDate", "MitigateDate"],
        },
        "steps": steps,
        "resources": {
            "clusters": clusters,
            "connectors": [
                _connector_block(c, selection)
                for c in _needed_connectors(selection, connectors or [])
            ],
            "skillRoots": sorted(
                {str(item.directory.parent) for item in selection.skills}
            ),
            "auth": "az login",
        },
    }


def _access(cluster: str) -> dict[str, Any]:
    from .dataplanes import access_for, load_access

    host = cluster.replace("https://", "").replace("http://", "").strip("/")
    access = access_for(host, load_access())
    if not access.documented:
        return {"access": None}
    return {
        "access": {
            "requirement": access.requirement,
            "requestUrl": access.request_url,
            "documentedIn": access.source,
        }
    }


def _needed_connectors(selection: Selection, connectors) -> list:
    """Connectors this payload needs, from both the queries and the skills.

    Deriving these from the queries alone left the address book wrong where it
    mattered: `network` names geneva-mcp, workiq and icm in its own
    instructions, and a manifest listing only `azure` tells the agent it has
    everything it needs when it does not.

    Skill requirements are read from the skill's prose by `annotate_requirements`,
    so they are indicative rather than declared -- which is why they are marked
    as such in the output rather than presented as a contract.
    """
    wanted: set[str] = set()
    if selection.queries:
        wanted.add("azure")
        for query in selection.queries:
            if "icmcluster" in query.cluster:
                wanted.add("icm")

    chosen = {item.skill_id for item in selection.skills}
    for connector in connectors:
        if chosen & set(getattr(connector, "required_by", []) or []):
            wanted.add(connector.name)

    return [c for c in connectors if c.name in wanted]


def _connector_block(connector, selection: Selection) -> dict[str, Any]:
    chosen = {item.skill_id for item in selection.skills}
    named_by = sorted(chosen & set(getattr(connector, "required_by", []) or []))
    return {
        "name": connector.name,
        "type": connector.kind,
        "target": connector.target,
        "status": connector.status,
        "purpose": connector.purpose or None,
        #: Which selected skills mention it. Read from skill prose, so this is
        #: indicative rather than a declaration by the skill.
        "namedBySkills": named_by or None,
    }


def render(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def manifest_path(incident: Incident, config) -> Path:
    directory = config.output_dir / incident.incident_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "payload.json"


def write_manifest(incident: Incident, manifest: dict[str, Any], config) -> Path:
    path = manifest_path(incident, config)
    path.write_text(render(manifest), encoding="utf-8")
    return path
