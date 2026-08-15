"""Skill discovery, prompt construction and the permission model."""

from __future__ import annotations

from pathlib import Path

import pytest

from oce_sentry.copilot import (
    PROMPT_ARG_BUDGET,
    _command_length,
    build_command,
    build_prompt,
    parse_run_output,
    shell_escalation_enabled,
    strip_trace,
    write_instruction,
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


# ------------------------------------------------- command-line length limits


def _huge_skill(chars: int) -> Skill:
    return _skill(id="huge", name="Huge", body="x" * chars)


def test_large_skill_is_referenced_not_inlined(tmp_path):
    """A 150K skill body cannot be passed as an argument.

    CreateProcess caps the whole command line at 32767 characters. Sentry's own
    skills were small enough that inlining always worked; 17 of the 70 ODSP
    skills exceed the cap outright, and five of the nine kits contain one, so
    this was a live failure and not a theoretical one.
    """
    pack = ContextPack(directory=tmp_path, incident_id="850000001", files=[])
    skill = _huge_skill(150_000)

    inlined = build_command(skill, _incident(), pack)
    assert _command_length(inlined) > PROMPT_ARG_BUDGET

    path = write_instruction(skill, pack)
    referenced = build_command(skill, _incident(), pack, instruction_path=path)

    assert _command_length(referenced) < 32767
    assert "x" * 1000 not in " ".join(referenced)
    assert str(path) in " ".join(referenced)


def test_instruction_lands_in_the_pack(tmp_path):
    """The pack must record what the run was given, not just its evidence."""
    pack = ContextPack(directory=tmp_path, incident_id="850000001", files=[])
    skill = _huge_skill(50_000)
    path = write_instruction(skill, pack)

    assert path.parent == tmp_path
    assert path.read_text(encoding="utf-8") == skill.body


def test_referenced_prompt_forbids_guessing():
    """A model that cannot read the file must stop, not improvise a task."""
    pack = ContextPack(directory=Path("."), incident_id="850000001", files=[])
    prompt = build_prompt(
        _huge_skill(50_000), _incident(), pack, instruction_path=Path("i.md")
    )
    assert "cannot read it, say so and stop" in prompt
    assert "xxxx" not in prompt


def test_small_skills_stay_inlined(tmp_path):
    """Inlining is the stronger path and stays the default.

    The model cannot fail to read what is already in its prompt, so the file
    indirection is a fallback rather than a uniform mechanism.
    """
    pack = ContextPack(directory=tmp_path, incident_id="850000001", files=[])
    command = build_command(_skill(body="Assess the blast radius."), _incident(), pack)
    assert "Assess the blast radius." in " ".join(command)
    assert _command_length(command) < PROMPT_ARG_BUDGET


def test_budget_leaves_room_for_the_rest_of_the_command_line():
    """The prompt is not the only thing on the command line."""
    assert PROMPT_ARG_BUDGET < 32767 / 2


# ------------------------------------------------------- tool-call narration

_REAL_TRACE = """I'll start by reading the instruction file.

● Read instruction.md
  └ L1:150 (149 lines read)

✗ List evidence pack files (shell)
  │ Get-ChildItem -Recurse "C:\\packs\\1" | Select-Object FullName
  └ Permission to run this tool was denied due to the following rules: `shell`

/ Search (glob)
  │ "**/*"
  └ 5 files found

## Incident 841552464 - Outage Pattern Analysis

Assessment: ISOLATED - single signature, single farm.
"""


def test_tool_narration_is_stripped_from_the_answer():
    """The operator sees the answer, not thirty lines of file reads.

    Both the kit view and the CLI print the first twenty lines of a result, and
    a skill that reads six files produced more narration than that before its
    first sentence -- so the answer was reliably off screen.
    """
    cleaned = strip_trace(_REAL_TRACE)
    assert "## Incident 841552464" in cleaned
    assert "Assessment: ISOLATED" in cleaned
    assert "Read instruction.md" not in cleaned
    assert "Get-ChildItem" not in cleaned
    assert "Search (glob)" not in cleaned
    # Prose between tool calls is the model talking, and is kept.
    assert "I'll start by reading" in cleaned


def test_stripping_does_not_leave_the_answer_full_of_holes():
    cleaned = strip_trace(_REAL_TRACE)
    assert "\n\n\n" not in cleaned


def test_pure_trace_output_is_returned_rather_than_blanked():
    """A run that produced only tool calls is what an operator most needs to see."""
    only_trace = "● Read a.md\n  └ 1 line read\n"
    assert strip_trace(only_trace).strip() == only_trace.strip()


def test_prose_about_paths_is_not_mistaken_for_a_tool_call():
    """The `/ Tool (kind)` header is matched narrowly for this reason."""
    prose = "/ is the repository root, not a tool call.\nSee /var/log (it rotates)."
    assert strip_trace(prose) == prose


def test_parse_run_output_strips_both_trailer_and_narration():
    text = _REAL_TRACE + "\nAI Credits 0.42\nResume copilot --resume=abc123def456\n"
    answer, session, credits = parse_run_output(text)
    assert "Read instruction.md" not in answer
    assert "AI Credits" not in answer
    assert "Assessment: ISOLATED" in answer
    assert session == "abc123def456"
    assert credits == 0.42


def test_cost_and_resume_are_read_from_stderr():
    """Copilot prints its summary block on stderr, not stdout.

    Parsing stdout alone recorded aiCredits as null on every run ever made,
    and lost the resume id needed to continue a session -- which is the
    information an operator most wants after an expensive run.
    """
    trailer = (
        "Changes    +0 -0\n"
        "AI Credits 107 (1m 24s)\n"
        "Tokens     536.1k\n"
        "Resume     copilot --resume=8a482038-9e54-4f1a-870d-6524d9535ea4\n"
    )
    answer, session, credits = parse_run_output("The answer.", trailer=trailer)
    assert credits == 107
    assert session == "8a482038-9e54-4f1a-870d-6524d9535ea4"
    # The trailer is telemetry, and must not reach the answer.
    assert answer == "The answer."


def test_other_stderr_content_does_not_leak_into_the_answer():
    noisy = "warning: something unrelated\nAI Credits 3\n"
    answer, _, credits = parse_run_output("The answer.", trailer=noisy)
    assert answer == "The answer."
    assert credits == 3


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
