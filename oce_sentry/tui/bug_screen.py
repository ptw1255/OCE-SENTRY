"""The bug tracker.

Every work item this tooling has filed -- by the fleet's noise-triage loop and
by an operator from this console -- with its live state from Azure DevOps.

Sorted by idle time rather than age. The question this screen answers is "what
has been filed and forgotten", and a bug filed months ago but touched yesterday
is being worked, while one filed last week and untouched since is not.
"""

from __future__ import annotations

import webbrowser

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..ado import AdoClient, AdoError, Bug, load_board
from ..models import SourceResult, utcnow


class BugScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("c", "create_bug", "New bug"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_bug", "Open in ADO"),
        Binding("t", "toggle_terminal", "Show closed"),
    ]

    def __init__(self, config, tokens, incident=None) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._incident = incident
        self._client = AdoClient(tokens, timeout=config.query_timeout)
        self._bugs: list[Bug] = []
        self._visible: list[Bug] = []
        self._show_terminal = False
        self._generation = 0
        self._loaded = False

    def action_create_bug(self):
        """File a bug, from the screen that tracks them.

        Filing and tracking are the same task a minute apart, and this is where
        an operator can see whether the thing they are about to report is
        already open.

        An incident is context, not a requirement: a TSG or process problem is
        worth filing whether or not a row happens to be selected on the queue.
        """
        from .bug_form import CreateBugScreen

        def _done(created: dict | None) -> None:
            if created:
                self._set_status(
                    f"created bug {created['id']}: {created['title'][:60]}"
                )
                self.refresh_bugs()

        self.app.push_screen(
            CreateBugScreen(self._config, self._tokens, self._incident), _done
        )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="bug-body"):
            yield DataTable(id="bug-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="bug-detail")
        yield Static("", id="bug-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#bug-table", DataTable)
        table.add_columns("ID", "STATE", "AGE", "IDLE", "SOURCE", "ASSIGNED", "TITLE")
        self.refresh_bugs()

    # ------------------------------------------------------------------ fetch

    def action_refresh(self) -> None:
        self.refresh_bugs()

    def refresh_bugs(self) -> None:
        self._generation += 1
        self._set_status("loading bugs from Azure DevOps ...")
        self._fetch(self._generation)

    @work(thread=True, exclusive=True, group="bugs")
    def _fetch(self, generation: int) -> None:
        board = load_board()
        try:
            bugs = self._client.list_bugs(board)
            result = SourceResult(name="bugs", data=bugs, fetched_at=utcnow())
        except AdoError as exc:
            result = SourceResult(name="bugs", data=[], fetched_at=utcnow(), error=str(exc))
        self.app.call_from_thread(self._apply, generation, result)

    def _apply(self, generation: int, result: SourceResult) -> None:
        if generation != self._generation:
            return

        if not result.ok:
            if self._loaded:
                self._set_status(f"[red]{result.error}[/red] - showing last known data")
            else:
                self._set_status(f"[red]{result.error}[/red]")
            return

        self._loaded = True
        self._bugs = result.data
        self._render_table()

    def _render_table(self) -> None:
        table = self.query_one("#bug-table", DataTable)
        table.clear()

        # Idle-first: the point of the view is what has stalled.
        self._visible = sorted(
            [b for b in self._bugs if self._show_terminal or not b.is_terminal],
            key=lambda b: -(b.idle_days() or 0),
        )

        for bug in self._visible:
            age = bug.age_days()
            idle = bug.idle_days()
            idle_cell = f"{idle:.0f}d" if idle is not None else "-"
            if idle is not None and idle > 14 and not bug.is_terminal:
                idle_cell = f"[red]{idle_cell}[/red]"
            elif idle is not None and idle > 7 and not bug.is_terminal:
                idle_cell = f"[yellow]{idle_cell}[/yellow]"

            state = bug.state
            if bug.is_terminal:
                state = f"[green]{state}[/green]"
            elif state == "New":
                state = f"[yellow]{state}[/yellow]"

            table.add_row(
                str(bug.id),
                state,
                f"{age:.0f}d" if age is not None else "-",
                idle_cell,
                "operator" if bug.from_console else "fleet",
                (bug.assigned_to or "-")[:18],
                bug.title[:70],
            )

        hidden = len(self._bugs) - len(self._visible)
        note = f" - {hidden} closed hidden (t)" if hidden and not self._show_terminal else ""
        stalled = sum(
            1 for b in self._visible if not b.is_terminal and (b.idle_days() or 0) > 14
        )
        stalled_note = f" - [red]{stalled} untouched >14d[/red]" if stalled else ""
        self._set_status(f"{len(self._visible)} bug(s){note}{stalled_note}")
        self._render_detail()

    # ----------------------------------------------------------------- detail

    def _current(self) -> Bug | None:
        table = self.query_one("#bug-table", DataTable)
        row = table.cursor_row
        if not self._visible or row is None or row < 0 or row >= len(self._visible):
            return None
        return self._visible[row]

    def on_data_table_row_highlighted(self) -> None:
        self._render_detail()

    def _render_detail(self) -> None:
        detail = self.query_one("#bug-detail", Static)
        bug = self._current()
        if bug is None:
            detail.update("No bug selected.")
            return

        idle = bug.idle_days()
        age = bug.age_days()
        lines = [
            f"[b]{bug.id}[/b]  {bug.state}",
            _escape(bug.title),
            "",
            f"assigned  {bug.assigned_to or '(unassigned)'}",
            f"filed by  {bug.created_by or '(unknown)'}"
            + ("  [dim](from this console)[/dim]" if bug.from_console else "  [dim](by the fleet)[/dim]"),
            f"age       {age:.0f} days" if age is not None else "age       unknown",
            f"idle      {idle:.0f} days since last change" if idle is not None else "",
            f"monitor   {_escape(bug.monitor_id) if bug.monitor_id else '(not derived from the title)'}",
            f"tags      {', '.join(bug.tags) if bug.tags else '(none)'}",
            "",
            f"[dim]{bug.url}[/dim]",
        ]
        detail.update("\n".join(line for line in lines if line))

    # ---------------------------------------------------------------- actions

    def action_toggle_terminal(self) -> None:
        self._show_terminal = not self._show_terminal
        self._render_table()

    def action_open_bug(self) -> None:
        bug = self._current()
        if bug:
            webbrowser.open(bug.url)

    def action_close(self) -> None:
        self.dismiss()

    def _set_status(self, message: str) -> None:
        self.query_one("#bug-status", Static).update(message)


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")
