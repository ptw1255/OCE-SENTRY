"""Kits: a named set of skills, run in order against one incident.

A skill answers a narrow question. A kit answers the question an on-call
engineer actually has -- "is this real and how big is it?" -- by running the
two or three skills that together settle it.

The distinction that matters: a kit is *not* a folder of artifacts and *not* a
Kusto query. Those exist elsewhere in Sentry and used to share this name, which
is why nobody could say what a kit was. A kit is a playbook over skills.

Kits are declared in `policy/kits.json` so the set can be reviewed and changed
without touching code. Resolution is deliberately loud: a kit naming a skill
that is not installed reports itself incomplete rather than quietly running
short, because a playbook that silently skips a step produces an answer the
operator will trust more than it deserves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import Incident
from .skills import Skill, discover_skills

KIT_POLICY = Path(__file__).parent / "policy" / "kits.json"


@dataclass
class Kit:
    id: str
    name: str
    question: str
    when: str
    #: Ids as declared, in run order.
    skill_ids: list[str] = field(default_factory=list)
    #: Resolved against what is actually installed, same order.
    skills: list[Skill] = field(default_factory=list)
    #: Declared but not installed.
    missing: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.skills)

    @property
    def complete(self) -> bool:
        return bool(self.skills) and not self.missing

    @property
    def needs_shell(self) -> bool:
        return any(s.needs_shell for s in self.skills)

    @property
    def coverage(self) -> str:
        """Stated on every row, so an incomplete kit cannot be mistaken."""
        total = len(self.skill_ids)
        if not self.skills:
            return f"0 of {total} installed"
        if self.missing:
            return f"{len(self.skills)} of {total} installed"
        return f"{total} skills"

    def applies(self, incident: Incident | None) -> bool:
        """Kits apply to any incident.

        Every skill in every kit is incident-agnostic reasoning; none of them
        declare a monitor id. Routing by component is the skill's job, not the
        kit's, and guessing here would hide kits an operator wanted.
        """
        return incident is not None


@dataclass
class KitStep:
    """One skill's result inside a kit run."""

    skill_id: str
    ok: bool
    answer: str = ""
    error: str = ""
    resume_command: str = ""
    duration_ms: int = 0
    credits: float | None = None
    skipped: bool = False


@dataclass
class KitRun:
    kit_id: str
    incident_id: str
    steps: list[KitStep] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps if not s.skipped)

    @property
    def credits(self) -> float | None:
        spent = [s.credits for s in self.steps if s.credits is not None]
        return sum(spent) if spent else None

    def summary(self) -> str:
        ran = [s for s in self.steps if not s.skipped]
        good = sum(1 for s in ran if s.ok)
        total_ms = sum(s.duration_ms for s in ran)
        state = "cancelled" if self.cancelled else "finished"
        # Cost is stated because a kit is several model sessions, and with
        # connectors wired a single one can run to three figures of credits.
        cost = f", {self.credits:g} credits" if self.credits is not None else ""
        return (
            f"{self.kit_id} {state}: {good}/{len(ran)} skills answered "
            f"in {total_ms / 1000:.0f}s{cost}"
        )


def _load_definitions(path: Path = KIT_POLICY) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [k for k in raw.get("kits", []) if isinstance(k, dict) and k.get("id")]


def load_kits(config, path: Path = KIT_POLICY) -> list[Kit]:
    """Every declared kit, resolved against installed skills.

    Kits with nothing installed are still returned. An operator who cloned only
    one of the two skill repositories needs to see that "Infrastructure sweep"
    exists and why it is empty, rather than conclude Sentry has no such kit.
    """
    installed = {s.id: s for s in discover_skills(config) if s.ok}
    kits: list[Kit] = []
    for entry in _load_definitions(path):
        declared = [str(s) for s in entry.get("skills", [])]
        resolved = [installed[i] for i in declared if i in installed]
        kits.append(
            Kit(
                id=str(entry["id"]),
                name=str(entry.get("name") or entry["id"]),
                question=str(entry.get("question", "")),
                when=str(entry.get("when", "")),
                skill_ids=declared,
                skills=resolved,
                missing=[i for i in declared if i not in installed],
            )
        )
    return kits


def find_kit(config, kit_id: str, path: Path = KIT_POLICY) -> Kit | None:
    return next((k for k in load_kits(config, path) if k.id == kit_id), None)


def run_kit(
    kit: Kit,
    incident: Incident,
    config,
    on_event=None,
    should_cancel=None,
) -> KitRun:
    """Run a kit's skills in order, one Copilot session each.

    Each skill runs independently and a failure does not stop the kit: the
    skills gather different evidence, and losing one is not a reason to lose
    the rest. Shell stays denied here regardless of what a skill asks for --
    a batch run is the worst place to hand out shell, because the operator is
    not reading each command as it goes.
    """
    from .actions import actions_for, discover_kits as discover_query_kits
    from .copilot import run_skill
    from .packs import build_pack

    run = KitRun(kit_id=kit.id, incident_id=incident.incident_id)

    query_kits = [a for a in actions_for(incident, discover_query_kits(config)) if a.kind == "kit"]
    pack = build_pack(incident, config, kits=query_kits)

    for skill in kit.skills:
        if should_cancel is not None and should_cancel():
            run.cancelled = True
            for remaining in kit.skills[len(run.steps) :]:
                run.steps.append(KitStep(skill_id=remaining.id, ok=False, skipped=True))
            break

        if on_event is not None:
            on_event("start", skill.id, None)

        try:
            result = run_skill(skill, incident, pack, config, allow_shell=False)
            step = KitStep(
                skill_id=skill.id,
                ok=result.ok,
                answer=(result.answer or "").strip(),
                error="" if result.ok else result.summary(),
                resume_command=result.resume_command or "",
                duration_ms=result.duration_ms,
                credits=result.credits,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
            step = KitStep(skill_id=skill.id, ok=False, error=str(exc))

        run.steps.append(step)
        if on_event is not None:
            on_event("done", skill.id, step)

    return run
