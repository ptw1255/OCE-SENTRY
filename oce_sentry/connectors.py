"""Connectors: the MCP servers a skill needs to reach live data.

Every skill Sentry runs is written against MCP servers -- Kusto for telemetry,
IcM for incident context, Geneva for monitor health. Without them a skill can
only summarise the evidence pack, which is why live runs kept saying "no Kusto
MCP servers are connected" and answering from base rates alone.

Two things are worth stating plainly, because both were invisible before:

*Declared* is not *reachable*. `.mcp.json` lists twelve servers; whether each
one can actually start depends on a command being on PATH or an endpoint
answering, and that differs per machine.

*Reachable* is not *connected*. An MCP server is not a daemon -- the Copilot
CLI spawns it per session. So "running" is the wrong question. The right ones
are: can it start, and is Sentry passing it to skill runs at all.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

#: Where an MCP config usually lives, in the order Sentry looks. The RCA agent
#: repository is the source of truth for ODSP: it is where the skills come
#: from, so it is where their servers are declared.
_CONFIG_CANDIDATES = (
    Path.home() / "repos" / "SRELivesite-RCAAgent" / ".mcp.json",
    Path.home() / ".copilot" / "mcp-config.json",
)

#: What each server is for, in an on-call engineer's terms. The config file
#: says how to start a server; it never says why you would want it.
PURPOSE = {
    "azure": "Kusto queries (read-only) - telemetry, IcM warehouse, SLI data",
    "icm": "IcM incident context, discussion entries, component health",
    "geneva-mcp": "Geneva monitor health, metrics, KQL-M queries",
    "drdashboard": "DR dashboard - farm and traffic state",
    "ecs": "ECS configuration and flight state",
    "fcm": "Flight, config and deployment correlation",
    "ado-msazure": "Azure DevOps (msazure) - work items, code, commits",
    "ado-onedrive": "Azure DevOps (onedrive) - work items, code, commits",
    "bluebird-mcp-odsp": "SPO.Core code and commit search",
    "workiq": "Work item queries",
    "enghub": "EngHub service and team metadata",
    "spo-request-insights-mcp-ppe": "Microtrace details by correlation id",
    "odspdirectory": "ODSP team and directory lookup",
}

#: Substrings that identify a connector in a skill's prose. Skills declare
#: their prerequisites in English rather than front matter, so requirements are
#: read from the body -- see correlation-ai's "Prerequisites" section, which
#: names five Kusto servers.
_MENTIONS = {
    "azure": ("kusto",),
    "icm": ("icm mcp", "icm-mcp", "icm_proxy"),
    "geneva-mcp": ("geneva-mcp", "geneva mcp", "dgrep"),
    "drdashboard": ("drdashboard",),
    "ecs": ("ecs mcp", "ecs-mcp"),
    "ado-msazure": ("ado-msazure",),
    "ado-onedrive": ("ado-onedrive",),
    "bluebird-mcp-odsp": ("bluebird",),
    "workiq": ("workiq",),
    "enghub": ("enghub",),
    "spo-request-insights-mcp-ppe": ("spo-request-insights", "microtrace"),
    "odspdirectory": ("odspdirectory",),
}


@dataclass
class Connector:
    name: str
    kind: str  # http | stdio
    #: The URL, or the command and its arguments.
    target: str
    #: The config file that declared it.
    source: Path | None = None
    #: ready | missing | unreachable | unknown
    status: str = "unknown"
    detail: str = ""
    purpose: str = ""
    #: Skills that name this connector in their prose.
    required_by: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ready"

    @property
    def command(self) -> str:
        return self.target.split()[0] if self.kind == "stdio" and self.target else ""


def config_path(config=None) -> Path | None:
    """The MCP config Sentry will use, if one can be found.

    `OCE_SENTRY_MCP_CONFIG` wins so an operator can point at their own file
    without moving anybody else's.
    """
    override = os.environ.get("OCE_SENTRY_MCP_CONFIG")
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    return next((p for p in _CONFIG_CANDIDATES if p.is_file()), None)


def mcp_enabled() -> bool:
    """Whether Sentry passes MCP servers to skill runs.

    Off by default and deliberately so. Connecting these servers lets a skill
    query production telemetry during a run, which is a real widening of what
    an action can reach, and it should be a decision rather than a default.
    """
    return os.environ.get("OCE_SENTRY_ENABLE_MCP", "0") == "1"


def load_connectors(config=None, path: Path | None = None) -> list[Connector]:
    """Every MCP server declared, unprobed."""
    path = path or config_path(config)
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    servers = raw.get("mcpServers") or raw.get("servers") or {}
    connectors: list[Connector] = []
    for name, spec in sorted(servers.items()):
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("type") or ("http" if spec.get("url") else "stdio"))
        if kind == "http":
            target = str(spec.get("url", ""))
        else:
            parts = [str(spec.get("command", ""))] + [str(a) for a in spec.get("args", [])]
            target = " ".join(p for p in parts if p)
        connectors.append(
            Connector(
                name=name,
                kind=kind,
                target=target,
                source=path,
                purpose=PURPOSE.get(name, ""),
            )
        )
    return connectors


def probe(connector: Connector, timeout: float = 4.0) -> Connector:
    """Decide whether this connector could start on this machine.

    HTTP endpoints are probed with a plain unauthenticated GET: any response at
    all proves reachability, including a 404 or 405, because an MCP endpoint is
    not obliged to answer a bare GET politely. Only a connection failure counts
    as unreachable.
    """
    if connector.kind == "http":
        try:
            import httpx

            response = httpx.get(connector.target, timeout=timeout, follow_redirects=True)
            connector.status = "ready"
            connector.detail = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001 - any failure means unreachable
            connector.status = "unreachable"
            connector.detail = type(exc).__name__
        return connector

    command = connector.command
    if not command:
        connector.status = "unknown"
        connector.detail = "no command declared"
        return connector

    found = shutil.which(command)
    if found:
        connector.status = "ready"
        connector.detail = found
        # The IcM proxy is launched by relative path, so it only resolves when
        # the working directory is the repository that declared it. Reporting
        # "ready" without that caveat would be wrong on most machines.
        if connector.source is not None and any(
            arg.endswith(".py") and not Path(arg).is_absolute()
            for arg in connector.target.split()[1:]
        ):
            script = connector.source.parent / connector.target.split()[-1]
            if not script.is_file():
                connector.status = "missing"
                connector.detail = f"script not found: {script}"
    else:
        connector.status = "missing"
        connector.detail = f"{command} is not on PATH"
    return connector


def annotate_requirements(connectors: list[Connector], skills) -> list[Connector]:
    """Record which skills name each connector.

    Read from the skill body because skills declare prerequisites in prose, not
    front matter. It is a heuristic and stated as one -- its purpose is to
    answer "if this is down, what stops working", which is otherwise a question
    nobody can answer until a skill fails mid-incident.
    """
    for connector in connectors:
        needles = _MENTIONS.get(connector.name, (connector.name.lower(),))
        for skill in skills:
            body = f"{skill.description}\n{skill.body}".lower()
            if any(needle in body for needle in needles):
                connector.required_by.append(skill.id)
    return connectors


def status_summary(connectors: list[Connector]) -> str:
    if not connectors:
        return "no MCP config found"
    ready = sum(1 for c in connectors if c.ok)
    return f"{ready} of {len(connectors)} ready"
