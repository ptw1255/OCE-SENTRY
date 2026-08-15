"""Filing a bug from an operator's note.

The flow is: the engineer types what is wrong, a skill drafts a well-formed bug
from that note plus whatever evidence Sentry holds, the engineer reads the draft,
and only then is anything created in Azure DevOps.

The draft step is not decoration. A bug filed straight from a one-line note is
usually unactionable, and a bug drafted by a model without a human reading it is
worse -- it is unactionable *and* confidently worded.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import timezone

from .ado import AdoClient, AdoError, Bug, load_board
from .models import Incident, utcnow
from .packs import build_pack
from .skills import Skill, load_internal_skill

#: What an OCE is most often filing about. Offered as a starting point rather
#: than a fixed taxonomy: the free-text note is the real input.
CATEGORIES = [
    ("noise", "Monitor is noisy or unactionable"),
    ("tsg", "TSG is missing, outdated, or wrong"),
    ("routing", "Incident routed to the wrong team"),
    ("process", "Process or tooling problem"),
    ("other", "Something else"),
]


class BugDraftError(RuntimeError):
    """The draft could not be produced. Never silently falls back to filing."""


@dataclass
class BugDraft:
    title: str
    body_html: str
    category: str
    note: str
    incident_id: str = ""
    session_id: str = ""
    credits: float | None = None
    raw: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.title.strip() and self.body_html.strip())


def find_skill(config) -> Skill | None:
    """The bug-drafting skill.

    Loaded by id rather than through discovery: `file-bug` is machinery behind
    this action, not something an operator browses to, and discovery now lists
    only ODSP's ADO-owned skills.
    """
    skill = load_internal_skill("file-bug")
    return skill if skill is not None and skill.ok else None


def parse_draft(text: str) -> tuple[str, str]:
    """Split the skill's reply into title and body.

    Tolerant on purpose: a model that adds a stray blank line or wraps the
    output in a code fence should not cost the operator their note.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    match = re.search(r"TITLE:\s*(.+)", cleaned)
    if not match:
        raise BugDraftError("The draft contained no TITLE: line.")
    title = match.group(1).strip()

    remainder = cleaned[match.end() :].lstrip()
    if remainder.startswith("---"):
        remainder = remainder[3:].lstrip()
    if not remainder.strip():
        raise BugDraftError("The draft contained a title but no body.")
    return title[:250], remainder.strip()


def _fallback_body(note: str, category: str, incident: Incident | None) -> str:
    """A bug the operator can still file when the model is unavailable.

    Their note is preserved verbatim. Being unable to reach a model should not
    mean losing the observation.
    """
    parts = [
        "<p><b>Reported by an on-call engineer.</b></p>",
        f"<p><b>Category:</b> {html.escape(category)}</p>",
        "<p><b>What they wrote:</b></p>",
        f"<p>{html.escape(note)}</p>",
    ]
    if incident is not None:
        parts.append(
            "<p><b>While looking at incident "
            f'<a href="{html.escape(incident.icm_url)}">{html.escape(incident.incident_id)}</a></b>'
            f" — {html.escape(incident.title)}"
            f" (Sev {incident.severity_label}, monitor "
            f"{html.escape(incident.monitor_id or 'none recorded')})</p>"
        )
    parts.append(
        "<p><em>Drafted without a model: this is the operator's note verbatim, "
        "not a summary of it.</em></p>"
    )
    parts.append("<p><em>Filed from OCE Sentry by an on-call engineer.</em></p>")
    return "\n".join(parts)


def draft_bug(
    note: str,
    category: str,
    config,
    incident: Incident | None = None,
    on_line=None,
) -> BugDraft:
    """Draft a bug from the operator's note.

    Runs the `file-bug` skill with the same permissions as any other skill --
    shell denied, scoped to the pack. Falls back to the operator's verbatim note
    if the skill is unavailable or its output cannot be parsed.
    """
    from .copilot import CopilotUnavailable, run_skill

    note = note.strip()
    if not note:
        raise BugDraftError("No description was given.")

    skill = find_skill(config)
    incident_id = incident.incident_id if incident else ""

    if skill is None or incident is None:
        # The skill runner needs an incident to build a pack around. A bug
        # filed with no incident selected is still worth filing.
        return BugDraft(
            title=_fallback_title(note, category),
            body_html=_fallback_body(note, category, incident),
            category=category,
            note=note,
            incident_id=incident_id,
        )

    pack = build_pack(incident, config)
    try:
        (pack.directory / "request.md").write_text(
            f"# Operator report\n\n"
            f"Category: {category}\n\n"
            f"## What the on-call engineer wrote\n\n{note}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise BugDraftError(f"Could not write the request into the pack: {exc}") from exc

    try:
        run = run_skill(skill, incident, pack, config, allow_shell=False, on_line=on_line)
    except CopilotUnavailable:
        return BugDraft(
            title=_fallback_title(note, category),
            body_html=_fallback_body(note, category, incident),
            category=category,
            note=note,
            incident_id=incident_id,
        )

    try:
        title, body = parse_draft(run.answer)
    except BugDraftError:
        # A malformed draft is not a reason to lose the operator's note.
        return BugDraft(
            title=_fallback_title(note, category),
            body_html=_fallback_body(note, category, incident),
            category=category,
            note=note,
            incident_id=incident_id,
            session_id=run.session_id,
            credits=run.credits,
            raw=run.answer,
        )

    body = _append_provenance(body, incident, note)
    return BugDraft(
        title=title,
        body_html=body,
        category=category,
        note=note,
        incident_id=incident_id,
        session_id=run.session_id,
        credits=run.credits,
        raw=run.answer,
    )


def _fallback_title(note: str, category: str) -> str:
    prefix = {
        "noise": "Monitor noise",
        "tsg": "TSG gap",
        "routing": "Routing",
        "process": "Process",
    }.get(category, "Operator report")
    summary = " ".join(note.split())
    if len(summary) > 100:
        summary = summary[:97].rstrip() + "..."
    return f"{prefix}: {summary}"


def _append_provenance(body: str, incident: Incident | None, note: str) -> str:
    """Attach the operator's own words and the incident link.

    The drafted prose is a summary; the note is what they actually said. Both
    belong in the bug, because the summary is the thing that can be wrong.
    """
    extra = ["", "<hr/>", "<p><b>Operator's original note</b></p>", f"<p>{html.escape(note)}</p>"]
    if incident is not None:
        extra.append(
            "<p><b>Filed while looking at incident "
            f'<a href="{html.escape(incident.icm_url)}">{html.escape(incident.incident_id)}</a></b>'
            f" (Sev {incident.severity_label}, monitor "
            f"{html.escape(incident.monitor_id or 'none recorded')}, "
            f"owner {html.escape(incident.owning_contact_alias or 'unassigned')})</p>"
        )
    extra.append(
        f"<p><em>Filed {utcnow().astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        "from OCE Sentry.</em></p>"
    )
    return body + "\n" + "\n".join(extra)


def file_bug(draft: BugDraft, client: AdoClient, dry_run: bool = False) -> dict:
    board = load_board()
    tags = [f"oce-{draft.category}"] if draft.category else []
    return client.create_bug(
        board=board,
        title=draft.title,
        description_html=draft.body_html,
        extra_tags=tags,
        dry_run=dry_run,
    )
