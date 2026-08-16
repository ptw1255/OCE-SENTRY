"""What an operator can put in a payload for a given incident.

Kept separate from `payload` so the assembly of the document stays free of
discovery: this module answers "what is available here", `payload` answers
"what does the handoff look like".
"""

from __future__ import annotations

from pathlib import Path

from .actions import actions_for, discover_kits
from .dataexplorer import kit_target
from .models import Incident
from .payload import QueryItem, SkillItem, has_placeholders, resolve_query, resolve_window

#: Which skills to offer first for a monitor. A lookup, not a judgement: the
#: console does not decide what is wrong, it offers the skills the SRE team
#: wrote for this shape of problem and lets the operator choose.
#:
#: A monitor with no entry falls back to the general set, which is the honest
#: answer -- "these apply to any incident" -- rather than a guess dressed up as
#: a recommendation.
SUGGESTED: dict[str, tuple[str, ...]] = {
    "ODSPSev3Alertstorm": ("triage-fleet", "outage-pattern", "icm-reliability-analysis"),
    "MicroservicePing": ("icm", "outage-pattern", "network"),
    "AnalysisModuleQos": ("icm", "impact", "unexpected-error"),
}

GENERAL: tuple[str, ...] = ("icm", "impact", "outage-pattern")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def available_queries(
    incident: Incident,
    config,
    window: tuple[str, str] | None = None,
    now=None,
) -> list[QueryItem]:
    """Investigation queries that match this incident, window already resolved.

    `window` is passed in by callers that have already resolved it. Resolving
    it here as well produced a payload whose stated window and whose query
    disagreed by the seconds between the two calls -- a small discrepancy, but
    the exact kind that makes a document nobody can reproduce.

    A kit whose cluster cannot be determined, or whose window will not resolve,
    is left out rather than offered half-built. An operator handing an agent a
    query with `INCIDENT_START` still in it has been given homework, not help.
    """
    if window is None:
        try:
            start, end, _ = resolve_window(incident, now=now)
        except Exception:  # noqa: BLE001 - WindowError and anything malformed
            return []
    else:
        start, end = window

    items: list[QueryItem] = []
    for action in actions_for(incident, discover_kits(config)):
        if action.kind != "kit" or action.directory is None:
            continue
        cluster, database = kit_target(action.directory)
        if not cluster or not database:
            continue
        raw = _read(action.directory / "investigate.kql")
        if not raw.strip():
            continue
        resolved = resolve_query(raw, start, end)
        if has_placeholders(resolved):
            continue
        items.append(
            QueryItem(
                kit_id=action.id,
                cluster=cluster,
                database=database,
                kql=resolved,
                directory=action.directory,
                base_rate_card=_read(action.directory / "README.md"),
            )
        )
    return items


def available_skills(incident: Incident, config) -> list[SkillItem]:
    """Every installed skill, with the ones written for this monitor first.

    All of them are offered. Filtering to a suggested handful would hide the
    specialist an operator actually wanted, and the ordering already carries
    the recommendation.
    """
    from .catalog import is_maintenance
    from .skills import discover_skills

    preferred = SUGGESTED.get(incident.monitor_id, GENERAL)
    rank = {skill_id: index for index, skill_id in enumerate(preferred)}

    items: list[SkillItem] = []
    for skill in discover_skills(config):
        if not skill.ok or is_maintenance(skill.id):
            continue
        items.append(
            SkillItem(
                skill_id=skill.id,
                name=skill.name,
                description=skill.description,
                directory=skill.directory,
                source_repo=_repo_of(skill.directory),
            )
        )

    items.sort(key=lambda item: (rank.get(item.skill_id, len(rank)), item.skill_id.lower()))
    return items


def suggested_skill_ids(incident: Incident) -> tuple[str, ...]:
    return SUGGESTED.get(incident.monitor_id, GENERAL)


def _repo_of(directory: Path | None) -> str:
    """The checkout a skill came from, for the payload's "where to get it".

    Walks up to the git root rather than assuming a layout, because the two
    ODSP repositories nest their skills at different depths.
    """
    if directory is None:
        return ""
    for parent in [directory, *directory.parents]:
        if (parent / ".git").exists():
            return parent.name
    return ""
