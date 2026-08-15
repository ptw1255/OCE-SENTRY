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
production credentials. Everything here follows from that.
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


def build_prompt(skill: Skill, incident: Incident, pack: ContextPack) -> str:
    """Skill instruction plus a pointer to the evidence.

    The incident's own text is never interpolated into the instruction. It sits
    in the pack as data, which keeps a hostile or merely awkward incident title
    from reading as instruction.
    """
    return (
        f"{skill.body}\n\n"
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


def build_command(
    skill: Skill,
    incident: Incident,
    pack: ContextPack,
    allow_shell: bool = False,
) -> list[str]:
    command = [
        find_copilot(),
        "-p",
        build_prompt(skill, incident, pack),
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
    return command


def parse_run_output(text: str) -> tuple[str, str, float | None]:
    """Split the CLI's trailer off the answer.

    Copilot prints a summary block (Changes / AI Credits / Tokens / Resume)
    after the response. That is telemetry about the run, not part of it, so it
    is captured separately rather than shown as the skill's answer.
    """
    session = ""
    credits: float | None = None

    match = _RESUME.search(text)
    if match:
        session = match.group(1)
    match = _CREDITS.search(text)
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
    return "\n".join(answer_lines).strip(), session, credits


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
    answer, session_id, credits = parse_run_output(raw)

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

        (target / f"{stem}.md").write_text(run.answer or run.stdout, encoding="utf-8")

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
