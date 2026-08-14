"""The live incident queue.

The KQL here is built the same way `collect-watchlist.ps1` builds it, from the
same `data-paths.json`: same team list, same severity branches, same
environment classifier, same `case()` precedence. That is deliberate. A
hand-written "equivalent" query would drift from the fleet's definition of scope
silently, and a console whose queue disagrees with the fleet's reports is worse
than no console.

The difference is when it runs: the fleet queries every 20 minutes and persists
state; this queries on demand, so what an OCE sees is live rather than up to 20
minutes old.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..kusto import KustoClient, KustoError
from ..models import Incident, SourceResult, utcnow


def _quote_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def build_environment_classifier(scope: dict) -> str:
    """Mirror of Build-EnvironmentClassifier in collect-watchlist.ps1.

    OccurringEnvironment is inconsistently populated, so classification also
    matches title tokens -- and DPRODMGD* farms are production, which a naive
    `OccurringEnvironment == 'PROD'` filter silently drops.
    """
    branches: list[str] = []
    for rule in scope.get("environments", {}).get("classification", []):
        tests: list[str] = []
        if "environmentEquals" in rule:
            tests.append(f"Env in ({_quote_list(rule['environmentEquals'])})")
        for prefix in rule.get("environmentStartsWith", []) or []:
            tests.append(f"Env startswith '{prefix}'")
        for token in rule.get("titleContains", []) or []:
            tests.append(f"TitleLower contains '{str(token).lower()}'")
        if not tests:
            continue
        branches.append(f"({' or '.join(tests)}), '{rule['class']}'")
    if not branches:
        return "'UNCLASSIFIED'"
    return f"case({', '.join(branches)}, 'UNCLASSIFIED')"


def build_watchlist_query(scope: dict, table: str, lookback_days: int) -> str:
    team_ids = ", ".join(str(t["id"]) for t in scope["teams"])
    if not team_ids:
        raise ValueError("Scope policy declares no owning teams.")

    auto_identities = _quote_list(scope["autoMitigation"]["identities"])
    included_envs = _quote_list(scope["environments"]["include"])
    terminal = _quote_list(scope["watchlist"]["terminalStatuses"])
    classifier = build_environment_classifier(scope)

    # Branch order is load-bearing: customer-reported and customer-impacting are
    # in scope at ANY severity, so they must be tested before the severity
    # branches or they would fall through and be dropped.
    return f"""{table}
| where OwningTeamId in ({team_ids})
| where CreateDate > ago({lookback_days}d)
| where IsPurged == false
| extend SevNorm = iff(Severity == 25, 2.5, todouble(Severity))
| extend IsAutoMitigated = (isnotnull(MitigateDate) and MitigatedBy in ({auto_identities}))
| extend Env = toupper(coalesce(OccurringEnvironment, '')), TitleLower = tolower(Title)
| extend EnvClass = {classifier}
| extend TrackReason = case(
    IncidentType == 'CustomerReported', 'customer-reported',
    IsCustomerImpacting == true, 'customer-impacting',
    Severity in (2, 25) and not(IsAutoMitigated) and EnvClass in ({included_envs}), 'sev2-or-2.5-not-auto',
    Severity in (2, 25) and not(IsAutoMitigated) and EnvClass == 'UNCLASSIFIED', 'sev2-or-2.5-unclassified-env',
    '')
| where TrackReason != ''
| extend IsTerminal = (Status in ({terminal}) or isnotnull(MitigateDate))
| extend MinutesOpen = todouble(datetime_diff('minute', coalesce(MitigateDate, now()), CreateDate))
| project IncidentId, Title, Severity, SevNorm, Status, IncidentType, TrackReason,
          MonitorId, OwningTeamId, OwningTeamName, OwningContactAlias,
          CreateDate, MitigateDate, MitigatedBy, IsTerminal, MinutesOpen,
          IsCustomerImpacting, EnvClass, TsgId
| order by SevNorm asc, CreateDate desc"""


def sort_incidents(incidents: list[Incident]) -> list[Incident]:
    """Customer impact first, then severity, then longest open.

    Sev 2.5 is stored as the integer 25, so sorting on the raw column ranks it
    below Sev 3 -- the normalised value is the only safe sort key.
    """
    return sorted(
        incidents,
        key=lambda i: (not i.is_customer_impacting, i.severity, -i.minutes_open),
    )


def load_watchlist_enrichment(path: Path | None) -> dict[str, dict[str, Any]]:
    """Tracking history from the fleet's local state, when reachable.

    Purely additive. Its absence is normal on any machine that is not running
    the daemon, so a failure here is swallowed rather than surfaced as a source
    error.
    """
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}

    enrichment: dict[str, dict[str, Any]] = {}
    for entry in payload.get("active", []) or []:
        incident_id = str(entry.get("incidentId", ""))
        if incident_id:
            enrichment[incident_id] = {
                "runs_tracked": entry.get("runsTracked"),
                "first_tracked_at": entry.get("firstTrackedAt"),
            }
    return enrichment


def fetch_incidents(
    config: Config,
    client: KustoClient,
    open_only: bool = True,
) -> SourceResult[list[Incident]]:
    icm = config.policy.icm
    query = build_watchlist_query(
        scope=config.policy.scope,
        table=icm["tables"]["incidents"],
        lookback_days=config.lookback_days,
    )

    try:
        result = client.query(
            cluster=icm["cluster"],
            database=icm["database"],
            query=query,
            auth_resource=icm.get("authResource"),
        )
    except KustoError as exc:
        return SourceResult(
            name="incidents",
            data=[],
            fetched_at=utcnow(),
            error=str(exc),
        )

    incidents = [Incident.from_row(row) for row in result.rows]
    if open_only:
        incidents = [i for i in incidents if not i.is_terminal]

    enrichment = load_watchlist_enrichment(config.watchlist_path)
    for incident in incidents:
        extra = enrichment.get(incident.incident_id)
        if extra:
            incident.runs_tracked = extra.get("runs_tracked")
            incident.first_tracked_at = extra.get("first_tracked_at")

    return SourceResult(
        name="incidents",
        data=sort_incidents(incidents),
        fetched_at=utcnow(),
        detail={
            "duration_ms": result.duration_ms,
            "rows_returned": result.row_count,
            "open": len(incidents),
            "lookback_days": config.lookback_days,
            "enriched": sum(1 for i in incidents if i.runs_tracked is not None),
            "policy": f"{config.policy.path.name}@{config.policy.short_hash}",
        },
    )
