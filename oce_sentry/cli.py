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


def render_slis(config: Config, tokens: TokenProvider, hours: int = 24) -> int:
    """Headless SLI report.

    Same parity rule as the incident queue: anything the TUI shows must be
    reachable from a script or an agent that cannot drive a terminal.
    """
    from .sources.slis import fetch_slis
    from .tui.sli_screen import _window_label

    client = KustoClient(tokens, timeout=config.query_timeout)
    result = fetch_slis(config, client, hours=hours)

    detail = result.detail
    print(f"cluster    {detail.get('cluster', '')}/{detail.get('database', '')}")
    print(f"window     trailing {hours}h")
    print(f"data       {result.age_label()}")
    print()

    if not result.data:
        print(f"slis unavailable: {result.error}", file=sys.stderr)
        return 1

    header = (
        f"{'SLI':<24} {'VALUE':>11} {'OBJ':>7} {'WINDOW':>7} {'BUDGET':>8}  "
        f"{'REQUESTS':>14} {'FAILURES':>12}"
    )
    print(header)
    print("-" * len(header))

    # The window travels with the row rather than only in the header: a burn
    # figure read out of context is the thing this column exists to prevent.
    window = _window_label(hours)

    over_budget = 0
    for sli in result.data:
        if not sli.ok:
            print(f"{sli.name:<24} {'unavailable':>11} {'':>7} {window:>7}   {sli.error}")
            continue
        burn = sli.error_budget_burn
        if burn is not None and burn > 100:
            over_budget += 1
        print(
            f"{sli.name:<24} {sli.value:>10.4f}% {sli.objective:>6g}% {window:>7} "
            f"{(f'{burn:.0f}%' if burn is not None else '-'):>8}  "
            f"{sli.denominator:>14,.0f} {sli.failures:>12,.0f}"
        )

    for sli in result.data:
        if not sli.ok or not sli.environments:
            continue
        print()
        print(f"{sli.name} by environment:")
        for entry in sli.environments:
            if entry.value is None:
                continue
            flag = "  OVER" if entry.value < sli.objective else ""
            print(f"  {entry.key:<16} {entry.value:>10.4f}%  {entry.denominator:>14,.0f}{flag}")

    if over_budget:
        print()
        print(f"{over_budget} SLI(s) over error budget.")
    return 0


def render_bugs(config: Config, tokens: TokenProvider, show_all: bool = False) -> int:
    """Headless bug tracker."""
    from .ado import AdoClient, AdoError, load_board

    client = AdoClient(tokens, timeout=config.query_timeout)
    board = load_board()
    try:
        bugs = client.list_bugs(board)
    except AdoError as exc:
        print(f"bugs unavailable: {exc}", file=sys.stderr)
        return 1

    print(f"board      {board['organization']}/{board['project']} - {board['areaPath']}")
    print(f"tag        {board['tag']}")
    print(f"assigned   {board['assignedTo']}")
    print()

    visible = sorted(
        [b for b in bugs if show_all or not b.is_terminal],
        key=lambda b: -(b.idle_days() or 0),
    )
    if not visible:
        print("No open bugs.")
        return 0

    header = f"{'ID':<9} {'STATE':<10} {'AGE':>6} {'IDLE':>6} {'SOURCE':<9} TITLE"
    print(header)
    print("-" * len(header))
    for bug in visible:
        age = bug.age_days()
        idle = bug.idle_days()
        flag = "  STALLED" if (idle or 0) > 14 and not bug.is_terminal else ""
        print(
            f"{bug.id:<9} {bug.state:<10} "
            f"{(f'{age:.0f}d' if age is not None else '-'):>6} "
            f"{(f'{idle:.0f}d' if idle is not None else '-'):>6} "
            f"{('operator' if bug.from_console else 'fleet'):<9} "
            f"{bug.title[:60]}{flag}"
        )

    hidden = len(bugs) - len(visible)
    if hidden:
        print(f"\n{hidden} closed bug(s) hidden; use --all to include them.")
    return 0


def create_bug_cli(
    config: Config,
    tokens: TokenProvider,
    note: str,
    category: str = "other",
    incident_id: str | None = None,
    dry_run: bool = False,
) -> int:
    """Headless bug filing.

    `--dry-run` prints the exact work item that would be created and stops.
    Given this is the console's only write, it is worth being able to see the
    payload without producing one.
    """
    from .ado import AdoClient, AdoError
    from .bugs import draft_bug, file_bug

    incident = None
    if incident_id:
        client = KustoClient(tokens, timeout=config.query_timeout)
        result = fetch_incidents(config, client)
        if not result.ok:
            print(f"incidents unavailable: {result.error}", file=sys.stderr)
            return 1
        incident = next((i for i in result.data if i.incident_id == str(incident_id)), None)
        if incident is None:
            print(f"Incident {incident_id} is not in the open in-scope queue.", file=sys.stderr)
            return 1

    print("drafting ...")
    draft = draft_bug(note, category, config, incident)

    print()
    print(f"TITLE: {draft.title}")
    print()
    print(draft.body_html)
    print()

    ado = AdoClient(tokens, timeout=config.query_timeout)
    try:
        created = file_bug(draft, ado, dry_run=dry_run)
    except AdoError as exc:
        print(f"could not create the work item: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        print("DRY RUN - nothing was created.")
        return 0

    print(f"created {created['id']}: {created['url']}")
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


