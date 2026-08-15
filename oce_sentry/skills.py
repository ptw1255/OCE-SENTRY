"""Skill discovery.

Skills are `SKILL.md` directories. Sentry ships a couple of its own and
discovers the rest from configured sources; it never copies someone else's
skill into this repository, for the same reason it never vendors a runbook --
a copy forks the moment its owner edits the original.

Front matter is optional. A skill with none is still runnable; it simply has no
opinion about which incidents it applies to.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BUNDLED_SKILLS = Path(__file__).parent / "skills"

_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Skill:
    id: str
    name: str
    description: str
    body: str
    source: str
    directory: Path
    #: Optional narrowing. Absent means the skill is offered for any incident,
    #: which is right for most of them -- "summarise this" applies to
    #: everything.
    applies_to: dict[str, Any] = field(default_factory=dict)
    #: Opt-in escalation. Absent means shell is denied, which is the default
    #: and the only setting that actually constrains anything.
    needs_shell: bool = False
    model: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def monitor_id(self) -> str:
        return str(self.applies_to.get("monitor_id", "") or "")


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Read the leading `---` block.

    Deliberately small: a handful of scalar keys, one nested mapping for
    `applies_to`. A full YAML parser is a dependency this does not need, and
    skills that require more structure than this are doing something the
    runner will not honour anyway.
    """
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text

    data: dict[str, Any] = {}
    current_map: str | None = None

    for raw in match.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue

        indented = raw[:1].isspace()
        line = raw.strip()
        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if indented and current_map:
            data.setdefault(current_map, {})[key] = value
            continue

        if not value:
            current_map = key
            data.setdefault(key, {})
            continue

        current_map = None
        if value.lower() in ("true", "false"):
            data[key] = value.lower() == "true"
        else:
            data[key] = value

    return data, text[match.end():]


def _load_skill(directory: Path, source: str) -> Skill | None:
    skill_file = directory / "SKILL.md"
    if not skill_file.is_file():
        return None

    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # Reported as broken rather than skipped. A skill that quietly
        # disappears is worse than one that visibly fails.
        return Skill(
            id=directory.name,
            name=directory.name,
            description="",
            body="",
            source=source,
            directory=directory,
            error=f"could not read SKILL.md: {exc}",
        )

    meta, body = _parse_front_matter(text)
    applies_to = meta.get("applies_to") or {}
    if not isinstance(applies_to, dict):
        applies_to = {}

    return Skill(
        id=directory.name,
        name=str(meta.get("name") or directory.name),
        description=str(meta.get("description") or "").strip(),
        body=body.strip(),
        source=source,
        directory=directory,
        applies_to=applies_to,
        needs_shell=bool(meta.get("needs_shell", False)),
        model=str(meta.get("model") or ""),
        error="" if body.strip() else "SKILL.md has no body",
    )


def _scan(root: Path, source: str) -> list[Skill]:
    if not root.is_dir():
        return []
    found: list[Skill] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        skill = _load_skill(entry, source)
        if skill is not None:
            found.append(skill)
    return found


def skill_sources(config) -> list[tuple[Path, str]]:
    """Where skills come from, in precedence order.

    A skill defined closer to the operator wins on id collision: their own
    copy of a skill should beat the one Sentry ships.
    """
    sources: list[tuple[Path, str]] = []

    configured = os.environ.get("OCE_SENTRY_SKILLS")
    if configured:
        sources.append((Path(configured).expanduser(), "configured"))

    home = Path.home() / ".copilot" / "skills"
    sources.append((home, "copilot-user"))

    sources.append((BUNDLED_SKILLS, "bundled"))
    return sources


def discover_skills(config) -> list[Skill]:
    seen: dict[str, Skill] = {}
    for root, source in skill_sources(config):
        for skill in _scan(root, source):
            seen.setdefault(skill.id, skill)
    return sorted(seen.values(), key=lambda s: (s.source != "bundled", s.name.lower()))


def skills_for(incident, skills: list[Skill]) -> list[Skill]:
    """Skills offered for an incident.

    A skill that names a `monitor_id` is offered only for that monitor;
    everything else is offered for every incident. Same rule as kits: return
    every candidate and let the operator choose.
    """
    offered: list[Skill] = []
    for skill in skills:
        if not skill.ok:
            continue
        wanted = skill.monitor_id
        if wanted and wanted != incident.monitor_id:
            continue
        offered.append(skill)
    return offered
