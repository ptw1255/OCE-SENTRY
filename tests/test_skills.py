"""Skill discovery, prompt construction and the permission model."""

from __future__ import annotations

from pathlib import Path

import pytest

from oce_sentry.copilot import (
    build_command,
    build_prompt,
    parse_run_output,
    shell_escalation_enabled,
)
from oce_sentry.packs import ContextPack
from oce_sentry.skills import (
    Skill,
    discover_skills,
    load_internal_skill,
    skills_for,
)
from oce_sentry.models import Incident


def _skill(**kwargs) -> Skill:
    base = dict(
        id="assess",
        name="Assess",
        description="d",
        body="Do the thing.",
        source="bundled",
        directory=Path("."),
    )
    base.update(kwargs)
    return Skill(**base)


def _incident(**kwargs) -> Incident:
    base = dict(
        incident_id="850000001",
        title='Nasty "quoted" | title with $(injection)',
        severity=2.0,
        severity_raw=2,
        status="ACTIVE",
        incident_type="LiveSite",
        track_reason="sev2-or-2.5-not-auto",
        monitor_id="MicroservicePing",
        owning_team_id="104519",
        owning_team_name="team",
        owning_contact_alias="alias",
        create_date="2026-01-01T00:00:00Z",
        mitigate_date=None,
        mitigated_by=None,
        is_terminal=False,
        minutes_open=60.0,
        is_customer_impacting=False,
        env_class="PROD",
        tsg_id="",
    )
    base.update(kwargs)
    return Incident(**base)


def _pack(tmp_path: Path) -> ContextPack:
    return ContextPack(directory=tmp_path, incident_id="850000001", files=[])


def test_discovery_is_ado_only():
    """Sentry lists ODSP's ADO-owned skills and nothing else.

    Bundled skills and the operator's personal `~/.copilot/skills` were both
    removed as sources: an incident console that lists a writing-voice skill
    beside a mitigation skill has stopped being an incident console.
    """
    skills = discover_skills(None)
    assert all(s.source == "ado" for s in skills), {
        s.id: s.source for s in skills if s.source != "ado"
    }


def test_sentrys_own_skills_are_not_browsable():
    ids = {s.id for s in discover_skills(None)}
    assert not ({"assess-blast-radius", "draft-enrichment", "handover-note"} & ids)


def test_file_bug_still_loads_internally():
    """Create Bug depends on it, so removing it from discovery must not break it."""
    skill = load_internal_skill("file-bug")
    assert skill is not None and skill.ok
    assert skill.source == "internal"
    assert not skill.needs_shell
    # And it stays out of the browsable list.
    assert "file-bug" not in {s.id for s in discover_skills(None)}


def test_internal_skill_lookup_is_bounded_to_bundled():
    assert load_internal_skill("does-not-exist") is None


def test_no_skill_source_without_configuration(monkeypatch):
    """With nothing configured there are no skills, rather than a fallback set."""
    monkeypatch.delenv("OCE_SENTRY_SKILLS", raising=False)
    assert discover_skills(None) == []


def test_skill_without_monitor_applies_to_every_incident():
    general = _skill()
    assert skills_for(_incident(monitor_id="anything"), [general]) == [general]


def test_skill_with_monitor_is_narrowed():
    narrow = _skill(applies_to={"monitor_id": "MicroservicePing"})
    assert skills_for(_incident(monitor_id="MicroservicePing"), [narrow]) == [narrow]
    assert skills_for(_incident(monitor_id="SomethingElse"), [narrow]) == []


def test_broken_skill_is_never_offered():
    assert skills_for(_incident(), [_skill(error="no body")]) == []


def test_shell_is_denied_by_default(tmp_path):
    command = build_command(_skill(), _incident(), _pack(tmp_path))
    assert "--deny-tool" in command
    assert command[command.index("--deny-tool") + 1] == "shell"
    assert "--allow-tool" not in command


def test_shell_is_granted_only_when_asked_for(tmp_path):
    command = build_command(_skill(needs_shell=True), _incident(), _pack(tmp_path), allow_shell=True)
    assert "--allow-tool" in command
    assert "--deny-tool" not in command


def test_pack_is_the_only_directory_exposed(tmp_path):
    command = build_command(_skill(), _incident(), _pack(tmp_path))
    assert command[command.index("--add-dir") + 1] == str(tmp_path)


def test_agent_never_blocks_on_a_question(tmp_path):
    # A headless run that stops to ask something waits forever: nobody is
    # watching the pipe.
    assert "--no-ask-user" in build_command(_skill(), _incident(), _pack(tmp_path))


def test_incident_title_is_not_interpolated_into_the_prompt(tmp_path):
    """Incident text is evidence, not instruction.

    Titles are attacker-adjacent: they carry quotes, pipes and shell-looking
    fragments. They belong in the pack as data, referenced by id.
    """
    incident = _incident()
    prompt = build_prompt(_skill(), incident, _pack(tmp_path))
    assert incident.title not in prompt
    assert incident.incident_id in prompt


def test_escalation_is_off_unless_the_machine_opts_in(monkeypatch):
    monkeypatch.delenv("OCE_SENTRY_ALLOW_SKILL_SHELL", raising=False)
    assert not shell_escalation_enabled()
    monkeypatch.setenv("OCE_SENTRY_ALLOW_SKILL_SHELL", "1")
    assert shell_escalation_enabled()


def test_run_trailer_is_split_from_the_answer():
    raw = (
        "Scope: one farm.\n"
        "\n"
        "Changes    +0 -0\n"
        "AI Credits 16.9 (32s)\n"
        "Tokens     up 116.6k\n"
        "Resume     copilot --resume=88f54ff0-72de-45be-a601-7c1b2ebc31a2\n"
    )
    answer, session, credits = parse_run_output(raw)
    assert answer == "Scope: one farm."
    assert session == "88f54ff0-72de-45be-a601-7c1b2ebc31a2"
    assert credits == 16.9


def test_missing_trailer_is_not_an_error():
    answer, session, credits = parse_run_output("just an answer")
    assert answer == "just an answer"
    assert session == ""
    assert credits is None
