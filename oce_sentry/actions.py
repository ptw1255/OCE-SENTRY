"""Runbook discovery and execution.

Two rules shape everything here:

* Nothing runs without an explicit human action. There is no auto-run, no
  "apply all", and no implicit execution on selection.
* Arguments are passed as a list and never through a shell. Incident titles are
  attacker-adjacent text -- they contain quotes, parentheses and paths -- and the
  signed-in user has production access.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path

from .config import Config
from .models import Incident, utcnow


@dataclass
class Action:
    id: str
    title: str
    kind: str  # "kit" | "link"
    source: str
    monitor_id: str = ""
    directory: Path | None = None
    url: str = ""
    #: Declared side effects. Empty means read-only. An action that does not
    #: declare its effects is treated as if it writes.
    writes: list[str] = field(default_factory=list)
    base_rate: dict[str, str] = field(default_factory=dict)

    @property
    def read_only(self) -> bool:
        return not self.writes


@dataclass
class ActionRun:
    run_id: str
    action_id: str
    incident_id: str
    command: list[str]
    exit_code: int | None
    duration_ms: int
    stdout: str
    stderr: str
    started_at: str
    timed_out: bool = False
    artifacts: list[str] = field(default_factory=list)
    output_path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self) -> str:
        if self.timed_out:
            return f"timed out after {self.duration_ms/1000:.0f}s"
        if self.exit_code != 0:
            return f"failed (exit {self.exit_code}) in {self.duration_ms/1000:.1f}s"
        # "Ran and found nothing" is evidence, not failure. Kits verify their
        # schema at build time precisely so these two cases stay distinguishable.
        return f"ok in {self.duration_ms/1000:.1f}s"


_BASE_RATE_PATTERNS = {
    "firings": re.compile(r"(\d+)\s+firings?", re.I),
    "auto_mitigated": re.compile(r"([\d.]+)%\s*auto[- ]mitigated", re.I),
    "median_ttm": re.compile(r"median (?:time to mitigate|ttm)[^\d]*(\d+)", re.I),
}


def _read_base_rate(kit_dir: Path) -> dict[str, str]:
    """Best-effort scrape of the kit's base-rate card, for display only.

    The card is prose written for a human. Nothing here is load-bearing; a miss
    shows less context, never a wrong number. A machine-readable kit index is
    tracked upstream (meta-livesite-agent-expander#139).
    """
    readme = kit_dir / "README.md"
    if not readme.is_file():
        return {}
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    found: dict[str, str] = {}
    for key, pattern in _BASE_RATE_PATTERNS.items():
        match = pattern.search(text)
        if match:
            found[key] = match.group(1)
    for line in text.splitlines():
        if line.lower().startswith("tsg:") or "eng.ms" in line and "tsg" not in found:
            found.setdefault("tsg", line.strip()[:200])
            break
    return found


def _read_monitor_id(kit_dir: Path) -> str:
    fragment = kit_dir / "monitor-entry.yaml.txt"
    if not fragment.is_file():
        return ""
    try:
        text = fragment.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r'monitor_id:\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def discover_kits(config: Config) -> list[Action]:
    kits_dir = config.kits_dir
    if kits_dir is None:
        return []

    actions: list[Action] = []
    for entry in sorted(kits_dir.iterdir()):
        runner = entry / "run.ps1"
        if not entry.is_dir() or not runner.is_file():
            continue
        actions.append(
            Action(
                id=entry.name,
                title=entry.name.replace("-", " "),
                kind="kit",
                source="fleet-kits",
                monitor_id=_read_monitor_id(entry),
                directory=entry,
                # Tracked upstream: run.ps1 writes result-*.json into its own
                # directory (meta-livesite-agent-expander#138). Declared so the
                # UI can warn rather than pretend the run is read-only.
                writes=[f"{entry.name}/result-*.json"],
                base_rate=_read_base_rate(entry),
            )
        )
    return actions


def actions_for(incident: Incident, actions: list[Action]) -> list[Action]:
    """Candidate actions for an incident.

    Returns every match rather than choosing one. `monitorId` does not uniquely
    identify a kit -- MicroservicePing maps to two, one per farm signature -- and
    silently picking the wrong farm's kit would run, return rows, and look
    authoritative (meta-livesite-agent-expander#139).
    """
    matches: list[Action] = []
    if incident.monitor_id:
        matches = [a for a in actions if a.monitor_id and a.monitor_id == incident.monitor_id]

    if incident.tsg_id:
        matches.append(
            Action(
                id=f"tsg-{incident.incident_id}",
                title="Open the TSG for this incident",
                kind="link",
                source="watchlist",
                url=incident.tsg_id,
            )
        )
    return matches


def _find_pwsh() -> str:
    for candidate in ("pwsh", "pwsh.exe", "powershell", "powershell.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    raise FileNotFoundError(
        "PowerShell 7 (pwsh) not found on PATH. Investigation kits are PowerShell scripts."
    )


def build_command(action: Action, incident: Incident) -> list[str]:
    """Resolve the command as an argument vector.

    Never a string, never through a shell: incident-derived values are data, and
    keeping them in their own argv slots is what stops them becoming syntax.
    """
    if action.kind != "kit" or action.directory is None:
        raise ValueError(f"Action {action.id} is not executable.")
    return [
        _find_pwsh(),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(action.directory / "run.ps1"),
        "-IncidentId",
        str(incident.incident_id),
    ]


def run_action(
    action: Action,
    incident: Incident,
    config: Config,
    timeout: int | None = None,
) -> ActionRun:
    command = build_command(action, incident)
    timeout = timeout or config.action_timeout
    run_id = uuid.uuid4().hex[:12]
    started = utcnow()

    directory = action.directory or Path.cwd()
    before = _snapshot(directory)

    clock = time.perf_counter()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(directory),
            check=False,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr) + f"\n[timed out after {timeout}s]"
        exit_code = None
    duration_ms = int((time.perf_counter() - clock) * 1000)

    artifacts = sorted(_snapshot(directory) - before)

    run = ActionRun(
        run_id=run_id,
        action_id=action.id,
        incident_id=incident.incident_id,
        command=command,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout=stdout or "",
        stderr=stderr or "",
        started_at=started.astimezone(timezone.utc).isoformat(),
        timed_out=timed_out,
        artifacts=artifacts,
    )
    run.output_path = _persist(run, config)
    return run


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _snapshot(directory: Path) -> set[str]:
    try:
        return {p.name for p in directory.iterdir() if p.is_file()}
    except OSError:
        return set()


def _persist(run: ActionRun, config: Config) -> Path | None:
    """Write the run under the console's own state directory.

    Never inside a repository -- config.load_config refuses an output directory
    inside the fleet checkout for the same reason.
    """
    try:
        target = config.output_dir / run.incident_id
        target.mkdir(parents=True, exist_ok=True)
        stem = f"{run.started_at.replace(':', '').replace('-', '')[:15]}-{run.action_id}-{run.run_id}"

        (target / f"{stem}.stdout.txt").write_text(run.stdout, encoding="utf-8")
        if run.stderr.strip():
            (target / f"{stem}.stderr.txt").write_text(run.stderr, encoding="utf-8")

        sidecar = {
            "runId": run.run_id,
            "actionId": run.action_id,
            "incidentId": run.incident_id,
            "command": run.command,
            "exitCode": run.exit_code,
            "timedOut": run.timed_out,
            "durationMs": run.duration_ms,
            "startedAt": run.started_at,
            "artifactsWrittenBesideTheKit": run.artifacts,
        }
        path = target / f"{stem}.json"
        path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None
