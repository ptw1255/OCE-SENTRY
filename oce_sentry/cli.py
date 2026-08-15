"""Headless mode.

`--once` exists so the console is usable without a terminal UI -- from a script,
a pipeline, or an agent that cannot drive a TUI. It is also the fastest way to
tell whether the data layer works, which is why it is built before the TUI.
"""

from __future__ import annotations

import sys

from .actions import actions_for, discover_kits, run_action
from .auth import AuthError, TokenProvider
from .config import Config
from .kusto import KustoClient
from .models import Incident
from .sources.incidents import fetch_incidents


def _flag(incident: Incident) -> str:
    if incident.is_customer_impacting:
        return "CUST"
    if incident.is_stale:
        return "STALE"
    return ""


def render_once(config: Config, tokens: TokenProvider, limit: int = 50) -> int:
    try:
        account = tokens.signed_in_as()
    except AuthError as exc:
        print(f"auth: {exc}", file=sys.stderr)
        return 2

    print(f"identity   {account}")
    print(f"policy     {config.policy.label}  ({config.policy.path})")
    if config.policy.origin == "bundled" and config.policy.seeded_from:
        print(f"           seeded from {config.policy.seeded_from}")
    print(f"cluster    {config.policy.icm['cluster']}/{config.policy.icm['database']}")
    print(f"lookback   {config.lookback_days}d")
    print(f"state      {config.state_dir}")

    kits = discover_kits(config)
    if config.kits_dir:
        print(f"kits       {len(kits)} discovered from {config.kits_dir}")
    else:
        print("kits       none configured (set OCE_SENTRY_KITS to enable runbooks)")
    print()

    client = KustoClient(tokens, timeout=config.query_timeout)
    result = fetch_incidents(config, client)

    if not result.ok:
        print(f"incidents  UNAVAILABLE: {result.error}", file=sys.stderr)
        return 1

    detail = result.detail
    print(
        f"incidents  {len(result.data)} open of {detail['rows_returned']} in scope "
        f"({detail['duration_ms']}ms, enriched {detail['enriched']})"
    )
    print()

    if not result.data:
        print("Nothing open and in scope.")
        return 0

    header = f"{'SEV':<5} {'AGE':>7}  {'FLAG':<5} {'ENV':<12} {'OWNER':<14} {'ID':<16} TITLE"
    print(header)
    print("-" * min(len(header) + 40, 160))
    for incident in result.data[:limit]:
        age = f"{incident.hours_open:.0f}h" if incident.hours_open < 100 else f"{incident.hours_open/24:.0f}d"
        title = incident.title if len(incident.title) <= 70 else incident.title[:67] + "..."
        print(
            f"{incident.severity_label:<5} {age:>7}  {_flag(incident):<5} "
            f"{incident.env_class[:12]:<12} {incident.owning_contact_alias[:14]:<14} "
            f"{incident.incident_id:<16} {title}"
        )

    if len(result.data) > limit:
        print(f"... and {len(result.data) - limit} more")

    return 0


def render_actions(config: Config, tokens: TokenProvider, incident_id: str) -> int:
    client = KustoClient(tokens, timeout=config.query_timeout)
    result = fetch_incidents(config, client)
    if not result.ok:
        print(f"incidents unavailable: {result.error}", file=sys.stderr)
        return 1

    match = next((i for i in result.data if i.incident_id == str(incident_id)), None)
    if match is None:
        print(f"Incident {incident_id} is not in the open in-scope queue.", file=sys.stderr)
        return 1

    print(f"{match.incident_id}  Sev {match.severity_label}  {match.status}  {match.env_class}")
    print(f"  {match.title}")
    print(f"  owner {match.owning_contact_alias or '(unassigned)'}   monitor {match.monitor_id or '(none)'}")
    print(f"  reason {match.track_reason}   open {match.hours_open:.0f}h")
    if match.runs_tracked is not None:
        print(f"  fleet has looked at this {match.runs_tracked} time(s)")
    print()

    candidates = actions_for(match, discover_kits(config))
    if not candidates:
        print("No runbook matches this incident.")
        if not match.monitor_id:
            print("  (the incident carries no monitorId, so kits cannot be matched)")
        return 0

    print(f"{len(candidates)} candidate action(s):")
    for action in candidates:
        marker = "read-only" if action.read_only else f"WRITES {', '.join(action.writes)}"
        print(f"  [{action.kind}] {action.id}  ({marker})")
        if action.base_rate:
            bits = ", ".join(f"{k}={v}" for k, v in action.base_rate.items() if k != "tsg")
            if bits:
                print(f"      base rate: {bits}")
        if action.url:
            print(f"      {action.url}")
    return 0


def run_once_skill(config: Config, tokens: TokenProvider, incident_id: str, skill_id: str) -> int:
    """Headless skill execution.

    Parity with the TUI matters here for the same reason `--once` exists: the
    console should be usable by a script, a pipeline, or an agent that cannot
    drive a terminal UI.
    """
    from .copilot import CopilotUnavailable, run_skill, shell_escalation_enabled
    from .packs import build_pack, prune_packs
    from .skills import discover_skills

    client = KustoClient(tokens, timeout=config.query_timeout)
    result = fetch_incidents(config, client)
    if not result.ok:
        print(f"incidents unavailable: {result.error}", file=sys.stderr)
        return 1

    match = next((i for i in result.data if i.incident_id == str(incident_id)), None)
    if match is None:
        print(f"Incident {incident_id} is not in the open in-scope queue.", file=sys.stderr)
        return 1

    skill = next((s for s in discover_skills(config) if s.id == skill_id), None)
    if skill is None:
        available = ", ".join(s.id for s in discover_skills(config))
        print(f"No skill {skill_id!r}. Available: {available}", file=sys.stderr)
        return 1
    if not skill.ok:
        print(f"Skill {skill_id!r} is unusable: {skill.error}", file=sys.stderr)
        return 1

    kits = [a for a in actions_for(match, discover_kits(config)) if a.kind == "kit"]
    pack = build_pack(match, config, kits=kits)

    allow_shell = skill.needs_shell and shell_escalation_enabled()
    if skill.needs_shell and not allow_shell:
        print(
            "This skill asks for shell access, which is disabled on this machine.\n"
            "Set OCE_SENTRY_ALLOW_SKILL_SHELL=1 to permit it. Running without shell.",
            file=sys.stderr,
        )

    print(f"running skill {skill.id} against {match.incident_id}")
    print(f"  pack:  {pack.directory}")
    print(f"  shell: {'ALLOWED (full, as you)' if allow_shell else 'denied'}")
    print()

    try:
        run = run_skill(skill, match, pack, config, allow_shell=allow_shell, on_line=None)
    except CopilotUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(run.answer or "(no answer)")
    print()
    print(f"  {run.summary()}")
    if run.output_path:
        print(f"  saved:  {run.output_path}")
    if run.resume_command:
        print(f"  resume: {run.resume_command}")
    if run.stderr.strip():
        print("--- stderr ---", file=sys.stderr)
        print(run.stderr.strip()[:2000], file=sys.stderr)

    prune_packs(config)
    return 0 if run.ok else 1

    client = KustoClient(tokens, timeout=config.query_timeout)
    result = fetch_incidents(config, client)
    if not result.ok:
        print(f"incidents unavailable: {result.error}", file=sys.stderr)
        return 1

    match = next((i for i in result.data if i.incident_id == str(incident_id)), None)
    if match is None:
        print(f"Incident {incident_id} is not in the open in-scope queue.", file=sys.stderr)
        return 1

    action = next((a for a in discover_kits(config) if a.id == action_id), None)
    if action is None:
        print(f"No action {action_id!r}.", file=sys.stderr)
        return 1

    print(f"running {action.id} against {match.incident_id} ...")
    run = run_action(action, match, config)
    print(f"  {run.summary()}")
    if run.output_path:
        print(f"  output: {run.output_path}")
    if run.artifacts:
        print(f"  the kit also wrote beside itself: {', '.join(run.artifacts)}")
    print()
    print(run.stdout.rstrip()[:4000] or "(no stdout)")
    if run.stderr.strip():
        print("--- stderr ---", file=sys.stderr)
        print(run.stderr.rstrip()[:2000], file=sys.stderr)
    return 0 if run.ok else 1

