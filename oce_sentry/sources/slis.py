"""Service level indicators, read from the Geneva SLI data plane.

This is a different cluster from the incident queue and deliberately so. Geneva
publishes evaluated SLO windows to `genevaslidatafollower.westcentralus` /
`slidata`, keyed by ServiceTreeId rather than by service name -- which is why a
name-based search of that database finds nothing and concludes, wrongly, that a
service has no SLOs.

Sentry reads those windows. It does not compute reliability from raw telemetry:
recomputing an SLI from `PlatformEvent` or `AnalysisModuleQosEvent` produces a
number that disagrees with the one the SLO is actually measured on, and a
console that argues with Geneva about a service's reliability is worse than no
console.

Two SLIs are registered today. The registry is a list precisely so that adding
the third is a config change rather than a code change.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..kusto import KustoClient, KustoError
from ..models import SourceResult, utcnow

DEFAULT_CLUSTER = "https://genevaslidatafollower.westcentralus.kusto.windows.net"
DEFAULT_DATABASE = "slidata"

#: MeTA, Service Tree name "OneDrive Media Transform and Analysis".
#: Tables in slidata are named by ServiceTreeId, never by service name.
META_SERVICE_TREE_ID = "3b3948ac-a4b6-4bfb-889d-ec934d3dd759"

#: The registry. Version is pinned rather than discovered: a new Ver starts a
#: new table and the previous one stops receiving windows, so silently
#: following "highest Ver" would change what is being measured without anyone
#: deciding to.
DEFAULT_SLIS: list[dict[str, Any]] = [
    {
        "id": "analysis-reliability",
        "name": "Analysis Reliability",
        "table": f"{META_SERVICE_TREE_ID}.RawData.SuccessRateSLOs.Analysis Reliability.Ver3",
        "objective": 99.9,
        "description": "Success rate of the MeTA analysis pipeline.",
    },
    {
        "id": "web-reliability",
        "name": "Web Reliability",
        "table": f"{META_SERVICE_TREE_ID}.RawData.SuccessRateSLOs.Web Reliability.Ver3",
        "objective": 99.9,
        "description": "Success rate of the MeTA web tier.",
    },
]


@dataclass
class SliWindow:
    """One evaluated window, as Geneva recorded it."""

    start: str
    denominator: float
    numerator: float

    @property
    def value(self) -> float | None:
        return (100.0 * self.numerator / self.denominator) if self.denominator else None


@dataclass
class SliSlice:
    """A breakdown row: one environment, or one region."""

    key: str
    denominator: float
    numerator: float

    @property
    def value(self) -> float | None:
        return (100.0 * self.numerator / self.denominator) if self.denominator else None


@dataclass
class Sli:
    id: str
    name: str
    table: str
    objective: float
    description: str = ""
    denominator: float = 0.0
    numerator: float = 0.0
    windows: int = 0
    latest: str = ""
    trend: list[SliWindow] = field(default_factory=list)
    environments: list[SliSlice] = field(default_factory=list)
    regions: list[SliSlice] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def value(self) -> float | None:
        return (100.0 * self.numerator / self.denominator) if self.denominator else None

    @property
    def meeting_objective(self) -> bool | None:
        value = self.value
        return None if value is None else value >= self.objective

    @property
    def error_budget_burn(self) -> float | None:
        """Share of the allowed failure budget consumed in the window.

        More actionable than the raw percentage: 99.87% against a 99.9%
        objective sounds fine and is in fact 130% of the budget spent.
        """
        value = self.value
        if value is None:
            return None
        allowed = 100.0 - self.objective
        if allowed <= 0:
            return None
        return (100.0 - value) / allowed * 100.0

    @property
    def failures(self) -> float:
        return self.denominator - self.numerator


def load_registry() -> list[dict[str, Any]]:
    """Registered SLIs, overridable without a code change.

    `OCE_SENTRY_SLI_REGISTRY` points at a JSON file with the same shape as
    DEFAULT_SLIS. Adding an SLI should never require a release.
    """
    path = os.environ.get("OCE_SENTRY_SLI_REGISTRY")
    if not path:
        return list(DEFAULT_SLIS)

    try:
        raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"SLI registry at {path} could not be read: {exc}") from exc

    entries = raw.get("slis") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"SLI registry at {path} declares no SLIs.")

    for entry in entries:
        for required in ("id", "name", "table"):
            if required not in entry:
                raise ValueError(f"SLI registry entry missing {required!r}: {entry}")
        entry.setdefault("objective", 99.9)
        entry.setdefault("description", "")
    return entries


def _escaped(table: str) -> str:
    # Table names carry dots and spaces, so they must be bracket-quoted.
    return "['" + table.replace("'", "''") + "']"


def build_summary_query(table: str, hours: int) -> str:
    return f"""{_escaped(table)}
| where StartTimeUtc > ago({hours}h)
| summarize Denominator=sum(Denominator), Numerator=sum(Numerator),
            Windows=count(), Latest=max(EndTimeUtc)"""


def build_trend_query(table: str, hours: int, bucket_minutes: int) -> str:
    return f"""{_escaped(table)}
| where StartTimeUtc > ago({hours}h)
| summarize Denominator=sum(Denominator), Numerator=sum(Numerator)
        by Bucket=bin(StartTimeUtc, {bucket_minutes}m)
| order by Bucket asc"""


def build_slice_query(table: str, hours: int, column: str) -> str:
    """Breakdown by environment (`CustomerResourceId`) or region (`LocationId`)."""
    return f"""{_escaped(table)}
| where StartTimeUtc > ago({hours}h)
| summarize Denominator=sum(Denominator), Numerator=sum(Numerator) by Key=tostring({column})
| where Denominator > 0
| order by Denominator desc"""


def _f(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_sli(client: KustoClient, entry: dict, hours: int, cluster: str, database: str) -> Sli:
    sli = Sli(
        id=entry["id"],
        name=entry["name"],
        table=entry["table"],
        objective=float(entry.get("objective", 99.9)),
        description=entry.get("description", ""),
    )

    def run(query: str):
        return client.query(cluster=cluster, database=database, query=query)

    try:
        summary = run(build_summary_query(sli.table, hours))
    except KustoError as exc:
        sli.error = str(exc)
        return sli

    if summary.rows:
        row = summary.rows[0]
        sli.denominator = _f(row, "Denominator")
        sli.numerator = _f(row, "Numerator")
        sli.windows = int(_f(row, "Windows"))
        sli.latest = str(row.get("Latest") or "")

    if sli.denominator == 0:
        # No windows is a real state, not a failure: a stopped SLO version
        # looks exactly like this, and saying so is the point.
        sli.error = f"no evaluated windows in the last {hours}h"
        return sli

    bucket = 60 if hours <= 48 else 360
    try:
        trend = run(build_trend_query(sli.table, hours, bucket))
        sli.trend = [
            SliWindow(
                start=str(r.get("Bucket") or ""),
                denominator=_f(r, "Denominator"),
                numerator=_f(r, "Numerator"),
            )
            for r in trend.rows
        ]
    except KustoError:
        pass  # The headline number is still valid without a trend.

    for column, target in (("CustomerResourceId", "environments"), ("LocationId", "regions")):
        try:
            sliced = run(build_slice_query(sli.table, hours, column))
        except KustoError:
            continue
        setattr(
            sli,
            target,
            [
                SliSlice(
                    key=str(r.get("Key") or "(unset)"),
                    denominator=_f(r, "Denominator"),
                    numerator=_f(r, "Numerator"),
                )
                for r in sliced.rows
            ],
        )

    return sli


def fetch_slis(config, client: KustoClient, hours: int = 24) -> SourceResult[list[Sli]]:
    cluster = os.environ.get("OCE_SENTRY_SLI_CLUSTER", DEFAULT_CLUSTER)
    database = os.environ.get("OCE_SENTRY_SLI_DATABASE", DEFAULT_DATABASE)

    try:
        registry = load_registry()
    except ValueError as exc:
        return SourceResult(name="slis", data=[], fetched_at=utcnow(), error=str(exc))

    started = utcnow()
    slis = [fetch_sli(client, entry, hours, cluster, database) for entry in registry]

    # Each SLI fails independently: one unreadable table must not blank a view
    # that another SLI is populating correctly.
    failed = [s for s in slis if not s.ok]
    error = None
    if failed and len(failed) == len(slis):
        error = failed[0].error

    watermark = _latest_watermark(slis)
    return SourceResult(
        name="slis",
        data=slis,
        fetched_at=started,
        watermark=watermark,
        error=error,
        detail={
            "cluster": cluster,
            "database": database,
            "hours": hours,
            "registered": len(registry),
            "failed": len(failed),
        },
    )


def _latest_watermark(slis: list[Sli]) -> datetime | None:
    """Freshness of the data itself, not of the fetch.

    Geneva evaluates on a delay. Reporting fetch time as age would hide a
    stalled SLO pipeline behind a healthy-looking refresh.
    """
    latest: datetime | None = None
    for sli in slis:
        if not sli.latest:
            continue
        try:
            parsed = datetime.fromisoformat(sli.latest.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        latest = parsed if latest is None or parsed > latest else latest
    return latest
