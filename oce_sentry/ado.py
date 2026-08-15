"""Azure DevOps work items.

Two operations, deliberately: read the bugs this tooling has filed, and file a
new one. Nothing edits, closes or reassigns an existing item -- an OCE console
that can quietly change someone else's bug is a different and much larger trust
proposition than one that can add to the pile.

The board mapping is hard-coded per deployment rather than discovered, for the
same reason the fleet hard-codes it: values read from existing bugs are known
good, and a discovered area path that is subtly wrong files bugs somewhere
nobody looks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from .auth import TokenProvider

#: The ADO resource id. Constant across tenants.
ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"

DEFAULT_BOARD = {
    "organization": "onedrive",
    "project": "OneBranch",
    "workItemType": "Bug",
    "areaPath": "OneBranch\\NEXUS\\MeTA",
    "iterationPath": "OneBranch\\2026",
    "assignedTo": "parkerwall@microsoft.com",
    #: The fleet's noise bugs carry this. New bugs join them so one query finds
    #: everything, however it was filed.
    "tag": "meta-monitor-noise",
    #: Marks the ones a human filed from the console, so the two are still
    #: distinguishable within that set.
    "consoleTag": "oce-sentry",
    "apiVersion": "7.1",
    "fields": {
        "System.State": "New",
        "Microsoft.VSTS.Common.ValueArea": "Business",
        "Microsoft.VSTS.Common.Severity": "3 - Medium",
        "Microsoft.VSTS.Common.Priority": 3,
        "Custom.IsException": "False",
    },
    "terminalStates": ["Done", "Removed"],
}


class AdoError(RuntimeError):
    """An ADO call failed. The service's own message is preserved."""


@dataclass
class Bug:
    id: int
    title: str
    state: str
    assigned_to: str
    tags: list[str]
    created: str
    changed: str
    created_by: str = ""
    monitor_id: str = ""
    url: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in ("Done", "Removed", "Closed", "Resolved")

    @property
    def from_console(self) -> bool:
        return DEFAULT_BOARD["consoleTag"] in self.tags

    def age_days(self) -> float | None:
        return _age_days(self.created)

    def idle_days(self) -> float | None:
        """Days since anything happened to it.

        More useful than age for a triage view: a bug filed months ago and
        touched yesterday is being worked; one filed last week and untouched
        since is not.
        """
        return _age_days(self.changed)


def _age_days(stamp: str) -> float | None:
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0


def load_board() -> dict:
    """Board mapping, overridable per deployment.

    `OCE_SENTRY_ADO_BOARD` points at a JSON file with the same shape. Sentry
    ships MeTA's because that is who it is for; another team changes a file
    rather than the code.
    """
    path = os.environ.get("OCE_SENTRY_ADO_BOARD")
    if not path:
        return dict(DEFAULT_BOARD)
    try:
        with open(path, encoding="utf-8") as handle:
            override = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AdoError(f"ADO board config at {path} could not be read: {exc}") from exc
    board = dict(DEFAULT_BOARD)
    board.update(override)
    return board


class AdoClient:
    def __init__(self, tokens: TokenProvider, timeout: int = 60) -> None:
        self._tokens = tokens
        self._timeout = timeout

    # -------------------------------------------------------------- plumbing

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tokens.token(ADO_RESOURCE)}"}

    def _request(self, method: str, url: str, **kwargs) -> Any:
        # Callers may override or extend the headers (the work-item create needs
        # a json-patch content type), so merge rather than passing both.
        headers = {**self._headers(), **(kwargs.pop("headers", None) or {})}
        try:
            response = httpx.request(
                method, url, headers=headers, timeout=self._timeout, **kwargs
            )
        except httpx.TimeoutException as exc:
            raise AdoError(f"Azure DevOps timed out after {self._timeout}s.") from exc
        except httpx.HTTPError as exc:
            raise AdoError(f"Could not reach Azure DevOps: {exc}") from exc

        if response.status_code >= 400:
            raise AdoError(_describe(response))
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise AdoError("Azure DevOps returned a non-JSON response.") from exc

    # ------------------------------------------------------------------ read

    def list_bugs(self, board: dict, limit: int = 200) -> list[Bug]:
        """Every work item carrying the tracking tag, newest activity first."""
        org, project = board["organization"], board["project"]
        version = board["apiVersion"]

        wiql = (
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.TeamProject] = '{project}' "
            f"AND [System.Tags] CONTAINS '{board['tag']}' "
            "ORDER BY [System.ChangedDate] DESC"
        )
        found = self._request(
            "POST",
            f"https://dev.azure.com/{org}/{project}/_apis/wit/wiql?api-version={version}",
            json={"query": wiql},
        )
        ids = [str(item["id"]) for item in (found or {}).get("workItems", [])][:limit]
        if not ids:
            return []

        fields = ",".join(
            [
                "System.Id",
                "System.Title",
                "System.State",
                "System.AssignedTo",
                "System.Tags",
                "System.CreatedDate",
                "System.ChangedDate",
                "System.CreatedBy",
            ]
        )

        bugs: list[Bug] = []
        # The batch endpoint caps at 200 ids per call.
        for start in range(0, len(ids), 200):
            chunk = ",".join(ids[start : start + 200])
            payload = self._request(
                "GET",
                f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems"
                f"?ids={chunk}&fields={fields}&api-version={version}",
            )
            for item in (payload or {}).get("value", []):
                bugs.append(_to_bug(item, org, project))
        return bugs

    # ----------------------------------------------------------------- write

    def create_bug(
        self,
        board: dict,
        title: str,
        description_html: str,
        extra_tags: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """File one bug. The only write this console performs."""
        tags = [board["tag"], board.get("consoleTag", "")] + list(extra_tags or [])
        tag_value = "; ".join(t for t in dict.fromkeys(tags) if t)

        operations = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.AreaPath", "value": board["areaPath"]},
            {"op": "add", "path": "/fields/System.IterationPath", "value": board["iterationPath"]},
            {"op": "add", "path": "/fields/System.AssignedTo", "value": board["assignedTo"]},
            {"op": "add", "path": "/fields/System.Tags", "value": tag_value},
            {
                "op": "add",
                "path": "/fields/Microsoft.VSTS.TCM.ReproSteps",
                "value": description_html,
            },
        ]
        for name, value in (board.get("fields") or {}).items():
            operations.append({"op": "add", "path": f"/fields/{name}", "value": value})

        if dry_run:
            return {"dryRun": True, "title": title, "operations": operations}

        org, project = board["organization"], board["project"]
        url = (
            f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/"
            f"${board['workItemType']}?api-version={board['apiVersion']}"
        )
        result = self._request(
            "POST",
            url,
            content=json.dumps(operations).encode("utf-8"),
            headers={**self._headers(), "Content-Type": "application/json-patch+json"},
        )
        work_item_id = result.get("id")
        return {
            "id": work_item_id,
            "title": title,
            "state": (result.get("fields") or {}).get("System.State", ""),
            "url": f"https://dev.azure.com/{org}/{project}/_workitems/edit/{work_item_id}",
        }


def derive_monitor_id(title: str) -> str:
    """Recover the monitor from a noise-bug title.

    The fleet's titles read "Monitor noise: <monitor> fires N times in W", so
    the monitor is recoverable without another ADO field. A free-text title
    yields nothing rather than a guess -- a wrong monitor id would join a bug
    to the wrong incident.
    """
    marker = "Monitor noise:"
    if not title.startswith(marker):
        return ""
    remainder = title[len(marker) :].strip()
    for splitter in (" fires ", " raises "):
        if splitter in remainder:
            return remainder.split(splitter)[0].strip()
    return remainder


def _to_bug(item: dict, org: str, project: str) -> Bug:
    fields = item.get("fields") or {}
    assigned = fields.get("System.AssignedTo") or {}
    created_by = fields.get("System.CreatedBy") or {}
    raw_tags = fields.get("System.Tags") or ""
    title = str(fields.get("System.Title") or "")

    return Bug(
        id=int(item.get("id", 0)),
        title=title,
        state=str(fields.get("System.State") or ""),
        assigned_to=str(assigned.get("displayName") or assigned.get("uniqueName") or ""),
        tags=[t.strip() for t in raw_tags.split(";") if t.strip()],
        created=str(fields.get("System.CreatedDate") or ""),
        changed=str(fields.get("System.ChangedDate") or ""),
        created_by=str(created_by.get("displayName") or ""),
        monitor_id=derive_monitor_id(title),
        url=f"https://dev.azure.com/{org}/{project}/_workitems/edit/{item.get('id')}",
    )


def _describe(response: httpx.Response) -> str:
    detail = ""
    try:
        body = response.json()
        detail = body.get("message") or body.get("value", {}).get("Message", "")
    except ValueError:
        detail = (response.text or "").strip()[:400]
    if response.status_code in (401, 403):
        return (
            f"Not authorised for Azure DevOps ({response.status_code}). "
            f"The signed-in identity may lack access to this project. {detail}".strip()
        )
    return f"Azure DevOps returned {response.status_code}. {detail}".strip()

