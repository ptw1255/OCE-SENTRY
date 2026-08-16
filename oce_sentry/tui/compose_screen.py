"""Composing a payload for one incident.

The operator picks what to hand over -- any number of queries, any number of
skills -- and Sentry writes a single file. The point of the screen is the path
at the bottom: it is what the operator gives their agent, so it is on screen
before, during and after the build rather than mentioned once when the write
finishes.

Nothing here reasons. Selection is the operator's; assembly is mechanical.
"""

from __future__ import annotations

import subprocess
import webbrowser
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ..compose import available_queries, available_skills, suggested_skill_ids
from ..models import Incident
from ..manifest import manifest_path
from ..models import utcnow
from ..payload import Selection, WindowError, fingerprint, resolve_window


def copy_to_clipboard(text: str) -> bool:
    """Put text on the Windows clipboard.

    `clip.exe` rather than the terminal's OSC 52 escape: the console is often
    run inside a terminal that does not forward it, and a copy that silently
    does nothing is worse than a key that says it failed.
    """
    try:
        subprocess.run(
            ["clip.exe"],
            input=text.encode("utf-16-le"),
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception:  # noqa: BLE001 - any failure means no clipboard
        return False


class ComposeScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("space", "toggle", "Select"),
        Binding("a", "select_suggested", "Suggested"),
        Binding("n", "select_none", "Clear"),
        Binding("w", "write", "Write payload"),
        Binding("c", "copy_path", "Copy path"),
        Binding("v", "copy_command", "Copy command"),
        Binding("o", "open_payload", "Open file"),
    ]

    def __init__(self, config, tokens, incident: Incident) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._incident = incident
        #: (kind, id, label, detail, object)
        self._rows: list[tuple[str, str, str, str, object]] = []
        self._chosen: set[tuple[str, str]] = set()
        self._window: tuple[str, str, str] | None = None
        self._error = ""
        self._written: Path | None = None
        self._digest = ""
        self._steps = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="cmp-head")
        with Vertical(id="cmp-body"):
            yield DataTable(id="cmp-table", cursor_type="row", zebra_stripes=True)
            with VerticalScroll(id="cmp-detail"):
                yield Static("", id="cmp-info")
        yield Static("", id="cmp-path")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#cmp-table", DataTable).add_columns("", "TYPE", "ITEM", "DETAIL")
        self._load()

    # ------------------------------------------------------------------ load

    def _load(self) -> None:
        try:
            self._window = resolve_window(self._incident)
        except WindowError as exc:
            self._error = str(exc)
            self._window = None

        self._rows = []
        if self._window is not None:
            start, end, _ = self._window
            for query in available_queries(self._incident, self._config, window=(start, end)):
                self._rows.append(
                    (
                        "query",
                        query.kit_id,
                        query.kit_id,
                        f"{query.host} / {query.database}",
                        query,
                    )
                )

        for skill in available_skills(self._incident, self._config):
            self._rows.append(
                ("skill", skill.skill_id, skill.skill_id, skill.description[:70], skill)
            )

        # Preselect what the monitor suggests, and every matching query. This
        # is a starting point rather than a recommendation: an operator who
        # wants something else changes it, and one who does not gets a useful
        # payload from a single keypress.
        self._chosen = {(kind, item_id) for kind, item_id, *_ in self._rows if kind == "query"}
        for skill_id in suggested_skill_ids(self._incident):
            if any(item_id == skill_id for kind, item_id, *_ in self._rows if kind == "skill"):
                self._chosen.add(("skill", skill_id))

        self._refresh_view()

    def _refresh_view(self) -> None:
        # Not named _render: that shadows Widget._render and the screen then
        # fails to composite at all.
        table = self.query_one("#cmp-table", DataTable)
        row = table.cursor_row
        table.clear()
        for kind, item_id, label, detail, _ in self._rows:
            mark = "[green]x[/green]" if (kind, item_id) in self._chosen else " "
            table.add_row(mark, kind, label[:44], detail[:56])
        if row is not None and 0 <= row < len(self._rows):
            table.move_cursor(row=row)

        queries = sum(1 for kind, _ in self._chosen if kind == "query")
        skills = len(self._chosen) - queries
        head = [
            f"[b]Payload for incident {self._incident.incident_id}[/b]",
            f"[dim]{_escape(self._incident.title[:110])}[/dim]",
        ]
        if self._window is not None:
            start, end, provenance = self._window
            head.append(f"window   {start} .. {end}   [dim]{provenance}[/dim]")
        if self._error:
            head.append(f"[red]{_escape(self._error)}[/red]")
        head.append(f"selected {queries} quer(ies), {skills} skill(s)   [dim]space to change[/dim]")
        self.query_one("#cmp-head", Static).update("\n".join(head))

        self._refresh_path()
        self._render_detail()

    def _refresh_path(self) -> None:
        """The path is the product. Show it whether or not it exists yet."""
        path = manifest_path(self._incident, self._config)
        if self._written is not None:
            state = f"[green]WRITTEN[/green] {self._steps} step(s)  {self._digest}"
        elif path.is_file():
            state = "[yellow]from an earlier build - w to refresh[/yellow]"
        else:
            state = "[dim]not written - press w[/dim]"
        self.query_one("#cmp-path", Static).update(
            f" POINT YOUR AGENT AT   {path}     {state}"
        )

    def _current(self):
        table = self.query_one("#cmp-table", DataTable)
        row = table.cursor_row
        if not self._rows or row is None or row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def on_data_table_row_highlighted(self) -> None:
        self._render_detail()

    def _render_detail(self) -> None:
        info = self.query_one("#cmp-info", Static)
        current = self._current()
        if current is None:
            info.update("Nothing to select for this incident.")
            return
        kind, item_id, _, _, obj = current

        if kind == "query":
            lines = [
                f"[b]{_escape(item_id)}[/b]",
                "",
                f"cluster   {obj.cluster}",
                f"database  {obj.database}",
                "",
                "[dim]The window is already substituted; this runs as written.[/dim]",
                "",
                "[b]Query[/b]",
                _escape(obj.kql.strip()[:1400]),
            ]
        else:
            lines = [
                f"[b]{_escape(item_id)}[/b]",
                "",
                _escape(obj.description or obj.name),
                "",
                f"instructions  {_escape(str(obj.instruction_path))}",
                f"source        {_escape(obj.source_repo or 'unknown')}",
            ]

        # The command is shown, not just copyable. An operator who cannot see
        # what v puts on their clipboard has to trust it, and this is the
        # handoff the whole screen exists to produce.
        path = manifest_path(self._incident, self._config)
        lines += [
            "",
            "[b]HANDOFF[/b]  [dim]v copies this, c copies just the path[/dim]",
            _escape(
                f'copilot -p "Work IcM incident {self._incident.incident_id}. '
                f'Read {path} first."'
            ),
        ]
        info.update("\n".join(lines))

    # --------------------------------------------------------------- editing

    def action_toggle(self) -> None:
        current = self._current()
        if current is None:
            return
        key = (current[0], current[1])
        self._chosen.symmetric_difference_update({key})
        self._written = None
        self._refresh_view()

    def action_select_suggested(self) -> None:
        self._chosen = {(kind, item_id) for kind, item_id, *_ in self._rows if kind == "query"}
        for skill_id in suggested_skill_ids(self._incident):
            self._chosen.add(("skill", skill_id))
        self._written = None
        self._refresh_view()

    def action_select_none(self) -> None:
        self._chosen = set()
        self._written = None
        self._refresh_view()

    # ---------------------------------------------------------------- output

    def action_write(self) -> None:
        if self._window is None:
            self._set_path_message(f"[red]{_escape(self._error)}[/red]")
            return

        from ..connectors import annotate_requirements, load_connectors
        from ..manifest import build_manifest, render, write_manifest
        from ..skills import discover_skills

        selection = Selection(
            queries=[obj for kind, item_id, _, _, obj in self._rows
                     if kind == "query" and (kind, item_id) in self._chosen],
            skills=[obj for kind, item_id, _, _, obj in self._rows
                    if kind == "skill" and (kind, item_id) in self._chosen],
        )
        # Annotate before building: the manifest reads required_by to work out
        # which connectors the chosen skills need, and an unannotated list
        # reports only what the queries need.
        connectors = load_connectors(self._config)
        annotate_requirements(connectors, [s for s in discover_skills(self._config) if s.ok])

        manifest = build_manifest(
            self._incident,
            selection,
            self._config,
            connectors=connectors,
            window=self._window,
            generated_at=utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        body = render(manifest)
        self._written = write_manifest(self._incident, manifest, self._config)
        self._digest = fingerprint(body)
        self._steps = len(manifest["sequence"])
        self._refresh_path()

    def action_copy_path(self) -> None:
        path = manifest_path(self._incident, self._config)
        ok = copy_to_clipboard(str(path))
        self._set_path_message(
            "[green]path copied[/green]" if ok else "[red]could not reach the clipboard[/red]"
        )

    def action_copy_command(self) -> None:
        """Copy a command that points the operator's agent at the file."""
        path = manifest_path(self._incident, self._config)
        command = (
            f'copilot -p "Work IcM incident {self._incident.incident_id}. '
            f'Read {path} first: a JSON manifest with the incident facts, the '
            f'queries to run with cluster, database and window already resolved, '
            f'and the skills to load with their file paths. Follow steps in order."'
        )
        ok = copy_to_clipboard(command)
        self._set_path_message(
            "[green]command copied[/green]" if ok else "[red]could not reach the clipboard[/red]"
        )

    def action_open_payload(self) -> None:
        path = manifest_path(self._incident, self._config)
        if path.is_file():
            webbrowser.open(path.as_uri())
        else:
            self._set_path_message("[yellow]nothing written yet - press w[/yellow]")

    def _set_path_message(self, message: str) -> None:
        path = manifest_path(self._incident, self._config)
        self.query_one("#cmp-path", Static).update(
            f" POINT YOUR AGENT AT   {path}     {message}"
        )

    def action_close(self) -> None:
        self.dismiss()


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")
