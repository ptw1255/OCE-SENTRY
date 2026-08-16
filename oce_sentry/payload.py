"""The agent payload.

Sentry aggregates; it does not solve. The on-call engineer has GitHub Copilot
CLI with Agency alongside this console, and that is where an incident gets
worked. What Sentry owes them is a single, complete, reproducible handoff: what
the incident is, which queries to run and against what, which skills to load
and where those live, and which connectors any of it requires.

Determinism is the whole point. Given the same incident and the same selection,
this module produces the same bytes -- every field is copied from data Sentry
already holds or read from a file on disk. Nothing is inferred, nothing is
generated, and no model is involved. An operator who reruns this an hour later
gets a payload that differs only where IcM itself changed.

The one computation is the incident window, and it is a substitution rather
than a judgement: kits carry `INCIDENT_START` and `INCIDENT_END` placeholders,
and their own runner fills them from the incident's create and mitigate dates.
Sentry holds both fields already, so it can resolve the query before it is
handed anywhere -- which is the difference between a payload the agent can run
and one it has to finish.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .models import Incident

#: What kits write in place of the incident window.
START = "INCIDENT_START"
END = "INCIDENT_END"

_ISO = "%Y-%m-%dT%H:%M:%SZ"


class WindowError(ValueError):
    """The incident window cannot be resolved, so no query is emitted.

    An inverted or zero-length window returns no rows from any table, which
    reads as "the service was healthy" rather than as a broken query. The kit's
    own runner refuses for the same reason.
    """


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def resolve_window(incident: Incident, now: datetime | None = None) -> tuple[str, str, str]:
    """The incident's window, and where it came from.

    Start is the incident's create date. End is its mitigate date, or the
    current time while it is still open -- the same rule the kit's runner
    applies, using the same two IcM fields Sentry already holds.
    """
    start = _parse(incident.create_date)
    if start is None:
        raise WindowError(f"incident {incident.incident_id} has no usable CreateDate")

    end = _parse(incident.mitigate_date)
    if end is None:
        end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        provenance = "CreateDate .. now (incident is still open)"
    else:
        provenance = "CreateDate .. MitigateDate"

    if end <= start:
        raise WindowError(
            f"window is empty or inverted: {start.strftime(_ISO)} .. {end.strftime(_ISO)}. "
            "An inverted window returns zero rows from every table, which reads as an "
            "all-clear rather than as a broken query."
        )
    return start.strftime(_ISO), end.strftime(_ISO), provenance


#: The kit tells a human to fill the window in by hand. Once Sentry has done
#: it, that instruction is not just redundant but wrong -- it reads as
#: "Replace datetime(2026-07-18T14:56:00Z) with the incident window", which
#: describes work that is already finished.
_OBSOLETE_INSTRUCTION = re.compile(
    r"^//\s*Replace\s+INCIDENT_START.*?(?:\n//.*?run\.ps1.*?$)?\n",
    re.M | re.S,
)


def resolve_query(kql: str, start: str, end: str) -> str:
    """Substitute the window placeholders, leaving everything else alone."""
    cleaned = _OBSOLETE_INSTRUCTION.sub(
        f"// Window substituted by OCE Sentry from the incident: {start} .. {end}\n",
        kql,
        count=1,
    )
    resolved = cleaned.replace(START, f"datetime({start})")
    return resolved.replace(END, f"datetime({end})")


def has_placeholders(kql: str) -> bool:
    return START in kql or END in kql


@dataclass
class QueryItem:
    """One investigation query, ready to run."""

    kit_id: str
    cluster: str
    database: str
    kql: str
    directory: Path | None = None
    #: Populated when the kit ships a precomputed base-rate card.
    base_rate_card: str = ""

    @property
    def host(self) -> str:
        return self.cluster.replace("https://", "").replace("http://", "").strip("/")


@dataclass
class SkillItem:
    """One skill the agent should load, and where it lives."""

    skill_id: str
    name: str
    description: str
    directory: Path
    source_repo: str = ""

    @property
    def instruction_path(self) -> Path:
        return self.directory / "SKILL.md"


@dataclass
class Selection:
    """What the operator chose to hand over.

    Any number of queries and any number of skills. An empty selection is
    valid and produces a payload with the incident facts alone, which is still
    a useful thing to paste at an agent.
    """

    queries: list[QueryItem] = field(default_factory=list)
    skills: list[SkillItem] = field(default_factory=list)
    kit_id: str = ""

    @property
    def empty(self) -> bool:
        return not self.queries and not self.skills


def _fence(body: str, lang: str = "") -> list[str]:
    return [f"```{lang}", body.rstrip(), "```"]


def build_payload(
    incident: Incident,
    selection: Selection,
    connectors=None,
    now: datetime | None = None,
    window: tuple[str, str, str] | None = None,
) -> str:
    """The handoff document, in full.

    Markdown because the operator reads it before their agent does, and both
    handle it. Sections are ordered the way the work happens: what this is,
    what to measure, what to reason with, what access any of it needs.

    `window` is accepted so the caller can resolve it once and use the same
    values for the queries and for the document. Resolving it twice put a
    two-second disagreement between what the payload said the window was and
    what its queries actually asked for.
    """
    start, end, provenance = window or resolve_window(incident, now=now)

    lines: list[str] = [
        f"# Incident {incident.incident_id}",
        "",
        f"**{incident.title}**",
        "",
        "Assembled by OCE Sentry. Every fact below is copied from IcM or from a",
        "file on disk -- nothing here was generated, and the same incident with",
        "the same selection produces the same document.",
        "",
        "## 1. What this is",
        "",
        f"- Severity: {incident.severity_label}",
        f"- Status: {incident.status}",
        f"- Environment: {incident.env_class}",
        f"- Owner: {incident.owning_contact_alias or 'unassigned'} ({incident.owning_team_name})",
        f"- Monitor: {incident.monitor_id or 'none recorded'}",
        f"- In scope because: {incident.track_reason}",
        f"- Opened: {incident.opened_at}",
        f"- Open for: {incident.hours_open:.0f}h"
        + ("  (past the 7-day staleness threshold)" if incident.is_stale else ""),
        f"- Customer impacting: {'yes' if incident.is_customer_impacting else 'no'}",
        f"- IcM: {incident.icm_url}",
    ]
    if incident.tsg_id and incident.tsg_id.lower().startswith("http"):
        lines.append(f"- TSG: {incident.tsg_id}")

    lines += [
        "",
        "### Investigation window",
        "",
        f"- Start: `{start}`",
        f"- End: `{end}`",
        f"- Source: {provenance}",
        "",
        "Queries below already carry this window. Do not widen it without saying so:",
        "an inverted or oversized window is the quiet way to conclude the wrong thing.",
    ]

    if incident.description:
        lines += ["", "## 2. What was reported", "", incident.description]
        section = 3
    else:
        section = 2

    # ---------------------------------------------------------------- queries

    lines += ["", f"## {section}. Queries to run", ""]
    section += 1
    if not selection.queries:
        lines.append("None selected.")
    else:
        for index, item in enumerate(selection.queries, 1):
            lines += [
                f"### {index}. `{item.kit_id}`",
                "",
                f"- Cluster: `{item.cluster}`",
                f"- Database: `{item.database}`",
                "- Window: already substituted, see above",
                "",
            ]
            lines += _fence(item.kql, "kusto")
            if item.base_rate_card:
                lines += [
                    "",
                    "<details><summary>Precomputed base rate for this condition</summary>",
                    "",
                    item.base_rate_card.rstrip(),
                    "",
                    "</details>",
                ]
            lines.append("")

    # ----------------------------------------------------------------- skills

    lines += [f"## {section}. Skills to load", ""]
    section += 1
    if not selection.skills:
        lines.append("None selected.")
    else:
        lines += [
            "Read each `SKILL.md` before acting on it. These are maintained by the",
            "ODSP SRE team in Azure DevOps -- load them from the paths below rather",
            "than from memory, because they change.",
            "",
        ]
        for item in selection.skills:
            lines += [
                f"### `{item.skill_id}`",
                "",
                f"- {item.description or item.name}",
                f"- Instructions: `{item.instruction_path}`",
            ]
            if item.source_repo:
                lines.append(f"- Source: {item.source_repo}")
            lines.append("")

    # ------------------------------------------------------------- connectors

    lines += [f"## {section}. Access this needs", ""]
    section += 1
    needed = _connectors_for(selection, connectors or [])
    if not needed:
        lines.append("No connector is required beyond `az login`.")
    else:
        lines += [
            "`az login` first; every cluster below authenticates through it.",
            "",
            "| Connector | Purpose | Status on the operator's machine |",
            "| --- | --- | --- |",
        ]
        for connector in needed:
            lines.append(
                f"| `{connector.name}` | {connector.purpose or '-'} | {connector.status} |"
            )
        lines.append("")

    clusters = sorted({(q.cluster, q.database) for q in selection.queries})
    if clusters:
        lines += ["Clusters referenced:", ""]
        for cluster, database in clusters:
            lines.append(f"- `{cluster}` / `{database}`")
        lines.append("")

    # ---------------------------------------------------------------- closing

    lines += [
        f"## {section}. What to do with this",
        "",
        "1. Run the queries above as written. The window is already correct.",
        "2. Load the skills named above and follow their instructions.",
        "3. Ground every number in a returned row. If the data does not answer",
        "   something, say so rather than estimating.",
        "",
        "OCE Sentry does not investigate and has not interpreted anything here.",
        "It selected, resolved and assembled; the reasoning is yours.",
    ]

    return "\n".join(lines) + "\n"


def _connectors_for(selection: Selection, connectors) -> list:
    """The connectors this payload actually needs.

    Only those matching a cluster in the selection, plus the Kusto server
    itself when there is any query at all. Listing all twelve would tell the
    agent nothing about what this particular handoff requires.
    """
    if not selection.queries:
        return []
    wanted = {"azure"}
    for query in selection.queries:
        if "icmcluster" in query.host:
            wanted.add("icm")
    return [c for c in connectors if c.name in wanted]


def payload_path(incident: Incident, config) -> Path:
    """Where the payload lands.

    One stable path per incident rather than a timestamped series: the operator
    points their agent at a file, and a path that changes on every build is a
    path they have to re-copy every time.
    """
    directory = config.output_dir / incident.incident_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "payload.md"


def write_payload(incident: Incident, body: str, config) -> Path:
    path = payload_path(incident, config)
    path.write_text(body, encoding="utf-8")
    return path


def fingerprint(body: str) -> str:
    """A short digest, so two payloads can be compared at a glance.

    Deterministic assembly is only a claim until someone can check it.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
