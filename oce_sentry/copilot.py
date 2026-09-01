"""Running a skill through Copilot CLI.

The permission model is the whole point of this module, so it is worth stating
plainly why it looks the way it does. Testing against Copilot CLI 1.0.81
established three things:

* `--deny-tool shell` is enforced. A prompt asking for `whoami` was refused
  with an explicit rule citation.
* `--allow-tool shell` works in non-interactive mode; `--allow-all-tools` is
  not required despite what the help text implies.
* `--allow-tool "shell(echo)"` does NOT restrict which command runs. Under that
  exact allowlist, `whoami` executed and returned the user's account.

So there is no "let this skill run only these commands". There is shell denied,
or shell granted in full -- as the signed-in user, on a machine holding
production {Credential}. Everything here follows from that.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path

from .models import Incident, utcnow
from .packs import ContextPack
from .skills import Skill

_RESUME = re.compile(r"--resume=([0-9a-fA-F-]{8,})")
_CREDITS = re.compile(r"AI Credits\s+([\d.]+)")


class CopilotUnavailable(RuntimeError):
    """Copilot CLI is not installed. The message names the fix."""


@dataclass
class SkillRun:
    run_id: str
    skill_id: str
    incident_id: str
    command: list[str]
    exit_code: int | None
    duration_ms: int
    stdout: str
    stderr: str
    started_at: str
    shell_allowed: bool
    pack_dir: str
    timed_out: bool = False
    session_id: str = ""
    credits: float | None = None
    model: str = ""
    output_path: Path | None = None
    answer: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self) -> str:
        if self.timed_out:
            return f"timed out after {self.duration_ms / 1000:.0f}s"
        if self.exit_code != 0:
            return f"failed (exit {self.exit_code}) in {self.duration_ms / 1000:.1f}s"
        cost = f", {self.credits:g} credits" if self.credits is not None else ""
        if not self.answer.strip():
            # Ran and produced nothing is not the same as failed.
            return f"ran, produced no answer in {self.duration_ms / 1000:.1f}s{cost}"
        return f"ok in {self.duration_ms / 1000:.1f}s{cost}"

    @property
    def resume_command(self) -> str:
        return f"copilot --resume={self.session_id}" if self.session_id else ""


def find_copilot() -> str:
    for candidate in ("copilot", "copilot.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    raise CopilotUnavailable(
        "GitHub Copilot CLI not found on PATH. Install it and run `copilot login`; "
        "skills are executed through it."
    )


def shell_escalation_enabled() -> bool:
    """Off unless the machine opts in.

    Granting shell is granting the agent everything the operator can reach, so
    it is a deliberate per-machine decision rather than a per-skill one.
    """
    return os.environ.get("OCE_SENTRY_ALLOW_SKILL_SHELL", "0") == "1"


#: CreateProcess caps an entire command line at 32767 characters on Windows,
#: and execve has comparable limits elsewhere. Sentry's own skills were a few
#: thousand characters, so passing the instruction as an argument worked until
#: the ODSP skills arrived: 17 of 70 exceed the cap outright, the largest at
#: 151K, and `icm`, `sql`, `redis`, `network` and `dns` are all over it. Those
#: sit in five of the nine kits, so the failure was not an edge case.
#:
#: The budget is deliberately well under the hard cap. The rest of the command
#: line -- the executable path, the pack directory, the flags -- also counts,
#: and a limit that only fails on the longest possible pack path is a limit
#: that fails in production rather than in testing.
PROMPT_ARG_BUDGET = 16000

#: Filename used when the instruction is too long to pass as an argument.
INSTRUCTION_FILE = "instruction.md"


def build_prompt(
    skill: Skill,
    incident: Incident,
    pack: ContextPack,
    instruction_path: Path | None = None,
) -> str:
    """Skill instruction plus a pointer to the evidence.

    The incident's own text is never interpolated into the instruction. It sits
    in the pack as data, which keeps a hostile or merely awkward incident title
    from reading as instruction.

    When `instruction_path` is given the skill body is referenced rather than
    inlined, because it does not fit in a command-line argument.
    """
    if instruction_path is not None:
        body = (
            "Your instructions for this task are in the file below. Read it in full "
            "before doing anything else, then carry it out.\n\n"
            f"    {instruction_path}\n\n"
            "That file is the complete task definition. Do not ask for it to be "
            "repeated and do not proceed on a guess about its contents: if you "
            "cannot read it, say so and stop."
        )
    else:
        body = skill.body

    return (
        f"{body}\n\n"
        "---\n"
        f"The evidence for this task is in: {pack.directory}\n"
        f"Start with context.md, then incident.json. base-rates.md, when present, is "
        "precomputed 90-day history for this condition. kit-results/ holds output from "
        "investigation queries the operator already ran.\n\n"
        f"The incident under investigation is {incident.incident_id}.\n"
        "Every number you state must come from that evidence. If the evidence does not "
        "answer something, say so rather than estimating. Be concise: an on-call engineer "
        "is reading this while holding a pager."
    )


def write_instruction(skill: Skill, pack: ContextPack) -> Path:
    """Put the skill body in the pack so it can be referenced, not passed.

    It lands in the pack rather than a scratch file because the pack is the
    record of what a run was given. A pack that holds the evidence but not the
    instruction cannot be used to explain an answer after the fact.
    """
    path = pack.directory / INSTRUCTION_FILE
    path.write_text(skill.body, encoding="utf-8")
    return path


def _command_length(command: list[str]) -> int:
    """Length of the command line as the OS will see it.

    Quoting adds to this, so the joined length is a floor rather than an exact
    figure -- which is why the budget sits well under the hard cap.
    """
    return sum(len(part) + 3 for part in command)


def build_command(
    skill: Skill,
    incident: Incident,
    pack: ContextPack,
    allow_shell: bool = False,
    instruction_path: Path | None = None,
) -> list[str]:
    command = [
        find_copilot(),
        "-p",
        build_prompt(skill, incident, pack, instruction_path=instruction_path),
        "--add-dir",
        str(pack.directory),
        "--no-ask-user",
        "--no-color",
        "--log-level",
        "none",
    ]
    if allow_shell:
        command += ["--allow-tool", "shell"]
    else:
        command += ["--deny-tool", "shell"]
    if skill.model:
        command += ["--model", skill.model]

    # Connectors are opt-in. Passing them lets a skill query production
    # telemetry during a run, which is a real widening of what an action
    # reaches, so it is a decision rather than a default. Without them a skill
    # can only summarise the evidence pack -- which is exactly what live runs
    # reported before this existed.
    from .connectors import config_path, mcp_enabled

    if mcp_enabled():
        mcp = config_path()
        if mcp is not None:
            command += ["--additional-mcp-config", f"@{mcp}"]
    return command


#: Copilot narrates its tool calls to stdout: a glyph, the tool name, then
#: indented argument and result lines.
#:
#:     ● Read instruction.md
#:       └ L1:150 (149 lines read)
#:     ✗ List evidence pack files (shell)
#:       │ Get-ChildItem -Recurse
#:       └ Permission to run this tool was denied
#:
#: Useful while streaming, noise afterwards. A skill that reads six files
#: produced thirty lines of this before its first sentence, and both the kit
#: view and the CLI show the first twenty lines of an answer -- so the operator
#: was reliably shown no answer at all.
_TRACE_GLYPHS = ("●", "✗", "✓", "│", "└", "├", "⏺", "⎿")

#: The tool header line, e.g. "/ Search (glob)". Matched narrowly because a
#: bare "/" prefix is plausible in real prose about paths.
_TOOL_HEADER = re.compile(r"^/ [A-Z][A-Za-z ]*\([a-z-]+\)\s*$")


def _is_trace(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.startswith(_TRACE_GLYPHS) or bool(_TOOL_HEADER.match(stripped))


def strip_trace(text: str) -> str:
    """Remove the tool-call narration, keeping the model's prose.

    Runs of blank lines left where a trace block was removed get collapsed --
    otherwise the answer arrives full of holes, and the first screen of a
    result is mostly whitespace.

    If stripping would leave nothing the original is returned instead: showing
    an empty answer is worse than showing a noisy one, and a run whose entire
    output was trace is exactly the case an operator needs to see.
    """
    kept: list[str] = []
    blanks = 0
    for line in text.splitlines():
        if _is_trace(line):
            continue
        if line.strip():
            blanks = 0
            kept.append(line)
            continue
        blanks += 1
        if blanks == 1:
            kept.append(line)

    cleaned = "\n".join(kept).strip()
    return cleaned or text.strip()


def parse_run_output(text: str, trailer: str = "") -> tuple[str, str, float | None]:
    """Split the CLI's trailer and tool narration off the answer.

    Copilot prints a summary block (Changes / AI Credits / Tokens / Resume)
    after the response, on **stderr** rather than stdout. That is telemetry
    about the run, not part of it, so it is read separately -- and `trailer`
    exists because parsing stdout alone silently lost the cost of every run and
    the resume id needed to continue a session.

    The answer is built from `text` only. Anything else on stderr is a warning
    or an error, and neither belongs in a skill's answer.
    """
    session = ""
    credits: float | None = None

    haystack = f"{text}\n{trailer}" if trailer else text
    match = _RESUME.search(haystack)
    if match:
        session = match.group(1)
    match = _CREDITS.search(haystack)
    if match:
        try:
            credits = float(match.group(1))
        except ValueError:
            credits = None

    answer_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("Changes ", "AI Credits", "Tokens ", "Resume ")):
            continue
        answer_lines.append(line)
    return strip_trace("\n".join(answer_lines)), session, credits


def run_skill(
    skill: Skill,
    incident: Incident,
    pack: ContextPack,
    config,
    allow_shell: bool = False,
    timeout: int | None = None,
    on_line=None,
) -> SkillRun:
    if allow_shell and not skill.needs_shell:
        allow_shell = False
    if allow_shell and not shell_escalation_enabled():
        raise PermissionError(
            "This skill asks for shell access, which is disabled on this machine. "
            "Set OCE_SENTRY_ALLOW_SKILL_SHELL=1 to permit it."
        )

    command = build_command(skill, incident, pack, allow_shell=allow_shell)
    # Fall back to referencing the instruction only when inlining will not fit.
    # Inlining is the stronger path -- the model cannot fail to read what is
    # already in its prompt -- so it stays the default rather than being
    # abandoned for uniformity.
    if _command_length(command) > PROMPT_ARG_BUDGET:
        instruction_path = write_instruction(skill, pack)
        command = build_command(
            skill,
            incident,
            pack,
            allow_shell=allow_shell,
            instruction_path=instruction_path,
        )
    timeout = timeout or config.action_timeout
    run_id = uuid.uuid4().hex[:12]
    started = utcnow()
    clock = time.perf_counter()

    collected: list[str] = []
    timed_out = False
    stderr_text = ""

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(pack.directory),
    )

    try:
        # Streamed rather than buffered: a skill takes tens of seconds, and a
        # silent pane is indistinguishable from a hang.
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\n")
            collected.append(line)
            if on_line is not None:
                on_line(line)
            if time.perf_counter() - clock > timeout:
                timed_out = True
                break
        if timed_out:
            _terminate(process)
        exit_code = process.wait(timeout=30)
        stderr_text = process.stderr.read() if process.stderr else ""
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate(process)
        exit_code = None
    finally:
        for stream in (process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    duration_ms = int((time.perf_counter() - clock) * 1000)
    raw = "\n".join(collected)
    answer, session_id, credits = parse_run_output(raw, trailer=stderr_text or "")

    run = SkillRun(
        run_id=run_id,
        skill_id=skill.id,
        incident_id=incident.incident_id,
        command=command,
        exit_code=None if timed_out else exit_code,
        duration_ms=duration_ms,
        stdout=raw,
        stderr=stderr_text or "",
        started_at=started.astimezone(timezone.utc).isoformat(),
        shell_allowed=allow_shell,
        pack_dir=str(pack.directory),
        timed_out=timed_out,
        session_id=session_id,
        credits=credits,
        model=skill.model,
        answer=answer,
    )
    run.output_path = _persist(run, config)
    return run


def _terminate(process: subprocess.Popen) -> None:
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:  # noqa: BLE001 - best effort; kill follows
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


def _persist(run: SkillRun, config) -> Path | None:
    try:
        target = config.output_dir / run.incident_id
        target.mkdir(parents=True, exist_ok=True)
        stem = f"{run.started_at.replace(':', '').replace('-', '')[:15]}-skill-{run.skill_id}-{run.run_id}"

        # The answer leads and the narration follows, rather than the answer
        # being buried thirty lines into a transcript. Both are kept: the trace
        # is how you tell "found nothing" apart from "was refused a tool".
        document = run.answer or run.stdout
        if run.stdout and run.stdout.strip() != (run.answer or "").strip():
            document = (
                f"{document}\n\n"
                "---\n\n"
                "<details>\n<summary>Full transcript, including tool calls</summary>\n\n"
                "```\n"
                f"{run.stdout.strip()}\n"
                "```\n\n</details>\n"
            )
        (target / f"{stem}.md").write_text(document, encoding="utf-8")

        sidecar = {
            "runId": run.run_id,
            "skillId": run.skill_id,
            "incidentId": run.incident_id,
            "sessionId": run.session_id,
            "resume": run.resume_command,
            "model": run.model,
            "aiCredits": run.credits,
            "durationMs": run.duration_ms,
            "exitCode": run.exit_code,
            "timedOut": run.timed_out,
            "shellAllowed": run.shell_allowed,
            "packDir": run.pack_dir,
            "startedAt": run.started_at,
            "command": run.command,
        }
        path = target / f"{stem}.json"
        path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None
