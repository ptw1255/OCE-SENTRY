"""Shared types.

`SourceResult` is the envelope every source returns. It exists so the UI can
always answer "how old is this, and where did it come from" without each tab
inventing its own answer -- and so a failed source degrades to stale data with a
visible reason rather than to an empty table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
import re
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Block-level tags whose boundaries are real line breaks in the rendered text.
_BLOCK_TAGS = re.compile(
    r"</?(?:p|div|br|tr|li|h[1-6]|table|blockquote|section)\b[^>]*>", re.I
)
_TAG = re.compile(r"<[^>]+>")
_BLANK_RUN = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    """Flatten IcM's HTML summary into something readable in a terminal.

    Deliberately small: this is display-only, the input is trusted IcM content,
    and pulling in a parser to render text nobody will style is not worth the
    dependency. Block tags become newlines so paragraphs survive; everything
    else is dropped and entities are unescaped.
    """
    if not html:
        return ""
    text = _BLOCK_TAGS.sub("\n", html)
    text = _TAG.sub("", text)
    text = unescape(text)
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    # Collapse trailing spaces per line, then runs of blank lines, so an email
    # pasted into IcM does not arrive as thirty empty rows.
    lines = [line.rstrip() for line in text.split("\n")]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


@dataclass
class SourceResult(Generic[T]):
    name: str
    data: T
    fetched_at: datetime
    #: When the underlying data was produced, when that is knowable. A published
    #: artifact carries its own generation time; a live query does not, and
    #: claiming otherwise would let a dead upstream hide behind a fresh fetch.
    watermark: datetime | None = None
    error: str | None = None
    stale: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    def age_seconds(self) -> float:
        reference = self.watermark or self.fetched_at
        return (utcnow() - reference).total_seconds()

    def age_label(self) -> str:
        if self.error and not self.data:
            return "unavailable"
        seconds = int(self.age_seconds())
        if seconds < 90:
            label = f"{seconds}s"
        elif seconds < 5400:
            label = f"{seconds // 60}m"
        else:
            label = f"{seconds // 3600}h"
        prefix = "data " if self.watermark else "fetched "
        return f"{prefix}{label} ago" + (" (stale)" if self.stale else "")


@dataclass
class Incident:
    incident_id: str
    title: str
    severity: float
    severity_raw: int
    status: str
    incident_type: str
    track_reason: str
    monitor_id: str
    owning_team_id: str
    owning_team_name: str
    owning_contact_alias: str
    create_date: str
    mitigate_date: str | None
    mitigated_by: str | None
    is_terminal: bool
    minutes_open: float
    is_customer_impacting: bool
    env_class: str
    tsg_id: str
    #: IcM's own summary, as HTML. Empty on plenty of incidents -- monitor
    #: filed ones often carry nothing -- so it is never assumed present.
    summary: str = ""
    #: Enrichment from the fleet's watchlist, when that file is reachable.
    #: Absent on any machine not running the daemon, and the queue does not
    #: depend on it.
    runs_tracked: int | None = None
    first_tracked_at: str | None = None

    @property
    def hours_open(self) -> float:
        return self.minutes_open / 60.0

    @property
    def opened_at(self) -> str:
        """The create date as a human reads it, in UTC.

        IcM returns an ISO timestamp with microseconds and a timezone suffix,
        which is precise and unreadable at a glance. "how long" and "since
        when" are different questions and an on-call engineer asks both.
        """
        raw = (self.create_date or "").strip()
        if not raw:
            return ""
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw[:16].replace("T", " ")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    @property
    def description(self) -> str:
        """IcM's summary as plain text.

        The field is HTML -- one live incident carried 7KB of Outlook markup --
        so it is stripped rather than rendered. Textual would treat stray
        square brackets as markup and silently swallow them, and an on-call
        engineer reading an incident description does not need the styling.
        """
        return _html_to_text(self.summary)

    @property
    def is_stale(self) -> bool:
        """Open longer than the 7 days the fleet's own aggregate report treats as stale."""
        return self.minutes_open >= 168 * 60

    @property
    def icm_url(self) -> str:
        return f"https://portal.microsofticm.com/imp/v3/incidents/details/{self.incident_id}/home"

    @property
    def severity_label(self) -> str:
        return f"{self.severity:g}"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Incident":
        def s(key: str, default: str = "") -> str:
            value = row.get(key)
            return default if value is None else str(value)

        def f(key: str, default: float = 0.0) -> float:
            value = row.get(key)
            try:
                return default if value is None else float(value)
            except (TypeError, ValueError):
                return default

        return cls(
            incident_id=s("IncidentId"),
            title=s("Title"),
            severity=f("SevNorm"),
            severity_raw=int(f("Severity")),
            status=s("Status"),
            incident_type=s("IncidentType"),
            track_reason=s("TrackReason"),
            monitor_id=s("MonitorId"),
            owning_team_id=s("OwningTeamId"),
            owning_team_name=s("OwningTeamName"),
            owning_contact_alias=s("OwningContactAlias"),
            create_date=s("CreateDate"),
            mitigate_date=row.get("MitigateDate"),
            mitigated_by=row.get("MitigatedBy"),
            is_terminal=bool(row.get("IsTerminal")),
            minutes_open=f("MinutesOpen"),
            is_customer_impacting=bool(row.get("IsCustomerImpacting")),
            env_class=s("EnvClass"),
            tsg_id=s("TsgId"),
            summary=s("Summary"),
        )
