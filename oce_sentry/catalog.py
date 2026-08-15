"""The action library.

One catalog of everything an on-call engineer can run, whatever produced it:
skills that reason through Copilot, investigation kits that run a verified Kusto
query, and links that just open something. Previously these were three separate
concepts that only ever surfaced when a monitor id happened to match, which left
most of the library invisible on most incidents.

An entry answers four questions before it is run: where it came from, what it
applies to, what it will execute, and what it changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .actions import Action, discover_kits
from .models import Incident
from .skills import Skill, discover_skills

#: Ordered by how a human should reach for them: reasoning that works on any
#: incident first, then condition-specific queries, then plain links.
SOURCE_ORDER = {"skill": 0, "kusto": 1, "link": 2}

SOURCE_LABEL = {
    "skill": "skill",
    "kusto": "kusto",
    "link": "link",
}

#: Skills that exist to build or maintain the agent fleet rather than to work
#: an incident. They are real skills and worth keeping reachable, but an on-call
#: console that lists "onboard a team" beside "assess blast radius" has the same
#: problem as an incident queue that shows everything: the useful rows stop
#: being findable.
MAINTENANCE_SKILLS = {
    "generate-skill",
    "onboard-team",
    "discover-monitors",
    "discover-monitors-agent",
    "find-all-geneva-monitors",
    "devbox",
    "updateoceagentconfig",
    "launchoceagent",
    "update-rca-overview-deck",
    "shiftleft-metadatacollection",
    "odsp-shift-left-prtesting",
    "improve-tsg",
    "moreinfo-suggest-questions",
    "moreinfo-telemetry-analysis",
    "os-toast",
    "repro-in-sandbox",
    "trace",
}

#: Evaluation and self-test harnesses. Never useful mid-incident.
_EVAL_PREFIXES = ("eval-", "test-")


def is_maintenance(skill_id: str) -> bool:
    lowered = skill_id.lower()
    return lowered in MAINTENANCE_SKILLS or lowered.startswith(_EVAL_PREFIXES)


@dataclass
class CatalogEntry:
    """One runnable thing."""

    id: str
    name: str
    source: str  # skill | kusto | link
    description: str = ""
    #: Empty means it applies to any incident, which is true of most skills.
    monitor_id: str = ""
    directory: Path | None = None
    url: str = ""
    #: Declared side effects. Empty means read-only.
    writes: list[str] = field(default_factory=list)
    needs_shell: bool = False
    #: Only for kusto kits: the precomputed base rate, and the card's verdict.
    base_rate: dict[str, str] = field(default_factory=dict)
    verdict: str = ""
    skill: Skill | None = None
    action: Action | None = None
    error: str = ""
    #: Builds or maintains the agent fleet rather than working an incident.
    maintenance: bool = False

    @property
    def read_only(self) -> bool:
        return not self.writes

    @property
    def applies_to(self) -> str:
        if self.source == "link":
            return "incident with a TSG"
        return self.monitor_id or "any incident"

    @property
    def executes(self) -> str:
        if self.source == "link":
            return "opens in a browser"
        if self.source == "skill":
            shell = "shell ALLOWED" if self.needs_shell else "no shell"
            return f"copilot, {shell}"
        return "kusto query, local"

    def applies(self, incident: Incident | None) -> bool:
        """Whether this entry is relevant to the selected incident.

        Entries that do not apply are still listed -- the library is worth
        browsing -- but they are marked, because offering an action that cannot
        run is worse than showing it greyed.
        """
        if incident is None:
            return not self.monitor_id and self.source != "link"
        if self.source == "link":
            return bool(incident.tsg_id)
        if not self.monitor_id:
            return True
        return self.monitor_id == incident.monitor_id


#: The card states its own conclusion in a bolded sentence, e.g.
#: "**3 of 84 firings were customer impacting.** This is not noise."
#: That sentence is the point of the kit; everything above it is the evidence.
_VERDICT = re.compile(r"^\s*-\s+\*\*(.+?)\*\*\s*(.*)$", re.M)


def read_verdict(kit_dir: Path) -> str:
    card = kit_dir / "README.md"
    if not card.is_file():
        return ""
    try:
        text = card.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    marker = "What the base rate implies"
    start = text.find(marker)
    if start < 0:
        return ""

    # Bound the search to that section. Without this the regex runs on into
    # Ownership and returns "Team: SHAREPOINTSNAP\..." as the verdict, which is
    # confidently wrong rather than merely absent.
    section = text[start + len(marker) :]
    end = section.find("\n## ")
    if end >= 0:
        section = section[:end]

    match = _VERDICT.search(section)
    if not match:
        return ""
    lead, rest = match.group(1).strip(), match.group(2).strip()
    return f"{lead} {rest}".strip()


def build_catalog(
    config,
    incident: Incident | None = None,
    include_maintenance: bool = False,
    include_queries: bool = False,
) -> list[CatalogEntry]:
    """The skill browser's contents.

    `include_queries` adds the fleet's Kusto kits. Off by default: they are a
    different concept from a skill -- a generated query folder keyed to one
    monitor -- and listing them here was a leftover from before kits and
    skills were separated. They stay reachable where they are useful: the
    queue runs the monitor-matched one with `x` and shows its verdict, and
    `--query-kits` lists the inventory.
    """
    entries: list[CatalogEntry] = []

    for skill in discover_skills(config):
        if not skill.ok:
            entries.append(
                CatalogEntry(
                    id=skill.id,
                    name=skill.name,
                    source="skill",
                    description=skill.description,
                    error=skill.error,
                )
            )
            continue
        entries.append(
            CatalogEntry(
                id=skill.id,
                name=skill.name,
                source="skill",
                description=skill.description,
                monitor_id=skill.monitor_id,
                directory=skill.directory,
                needs_shell=skill.needs_shell,
                writes=["shell access (full, as you)"] if skill.needs_shell else [],
                skill=skill,
                maintenance=is_maintenance(skill.id),
            )
        )

    for action in discover_kits(config) if include_queries else []:
        directory = action.directory
        entries.append(
            CatalogEntry(
                id=action.id,
                name=_kit_name(action, directory),
                source="kusto",
                description="Verified query plus 90 days of base rates for this condition.",
                monitor_id=action.monitor_id,
                directory=directory,
                writes=action.writes,
                base_rate=action.base_rate,
                verdict=read_verdict(directory) if directory else "",
                action=action,
            )
        )

    if incident is not None and incident.tsg_id:
        entries.append(
            CatalogEntry(
                id=f"tsg-{incident.incident_id}",
                name="Open the TSG for this incident",
                source="link",
                description="The troubleshooting guide IcM recorded for this monitor.",
                url=incident.tsg_id,
            )
        )

    if not include_maintenance:
        entries = [e for e in entries if not e.maintenance]

    entries.sort(key=lambda e: (SOURCE_ORDER.get(e.source, 9), e.name.lower()))
    return entries


def count_maintenance(config) -> int:
    """How many were hidden, so the status line can say so rather than pretend."""
    return sum(1 for s in discover_skills(config) if s.ok and is_maintenance(s.id))


def _kit_name(action: Action, directory: Path | None) -> str:
    """Prefer the card's own heading over the generated slug.

    The slug is a truncated title plus a cluster hash; the heading is the
    condition as a human wrote it.
    """
    if directory is not None:
        card = directory / "README.md"
        if card.is_file():
            try:
                for line in card.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("# "):
                        return line[2:].strip()
            except OSError:
                pass
    return action.title


def entries_for(config, incident: Incident | None) -> list[CatalogEntry]:
    """The subset that can run against this incident, in library order."""
    return [e for e in build_catalog(config, incident) if e.applies(incident) and not e.error]

