"""The action library.

Everything an on-call engineer can run, in one list: skills that reason through
Copilot, investigation kits that run a verified Kusto query, and links. Grouped
by source, each row runnable against the incident selected on the queue.

Previously this screen was an inventory of fleet-generated artifacts, and the
things an OCE could actually run only appeared on an incident whose monitor id
happened to match -- which for most of the queue is never, so most of the
library was invisible most of the time.
"""

from __future__ import annotations

import webbrowser

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from ..catalog import CatalogEntry, build_catalog
from ..models import Incident


class LibraryScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("x", "run", "Run"),
        Binding("r", "refresh", "Refresh"),
        Binding("v", "toggle_view", "Detail / source"),
        Binding("o", "open_source", "Open folder"),
    ]

    def __init__(self, config, tokens, incident: Incident | None = None) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._incident = incident
        self._entries: list[CatalogEntry] = []
        self._view = "detail"
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="lib-body"):
            yield DataTable(id="lib-table", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="lib-detail"):
                yield Static("", id="lib-summary")
                yield RichLog(id="lib-output", wrap=True, markup=True, highlight=False)
        yield Static("", id="lib-status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#lib-table", DataTable).add_columns(
            "SOURCE", "ACTION", "APPLIES TO", "EXECUTES", "EFFECT"
        )
        self.refresh_catalog()

    # ------------------------------------------------------------------ load

    def action_refresh(self) -> None:
        self.refresh_catalog()

    def refresh_catalog(self) -> None:
        self._entries = build_catalog(self._config, self._incident)
        table = self.query_one("#lib-table", DataTable)
        table.clear()

        for entry in self._entries:
            applicable = entry.applies(self._incident)
            name = entry.name[:52]
            if entry.error:
                name = f"[red]{name}[/red]"
            elif not applicable:
                # Listed but marked: offering an action that cannot run is
                # worse than showing it greyed.
                name = f"[dim]{name}[/dim]"

            effect = "[red]writes[/red]" if not entry.read_only else "read-only"
            if entry.needs_shell:
                effect = "[red]SHELL[/red]"

            table.add_row(
                entry.source,
                name,
                entry.applies_to[:24],
                entry.executes,
                effect,
            )

        runnable = sum(1 for e in self._entries if e.applies(self._incident) and not e.error)
        if self._incident is not None:
            context = (
                f"incident {self._incident.incident_id} - "
                f"monitor {self._incident.monitor_id or 'none recorded'}"
            )
        else:
            context = "no incident selected - condition-specific actions are shown greyed"
        self._set_status(f"{len(self._entries)} action(s), {runnable} runnable here - {context}")
        self._render_detail()

    # ---------------------------------------------------------------- detail

    def _current(self) -> CatalogEntry | None:
        table = self.query_one("#lib-table", DataTable)
        row = table.cursor_row
        if not self._entries or row is None or row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]

    def on_data_table_row_highlighted(self) -> None:
        self._render_detail()

    def action_toggle_view(self) -> None:
        self._view = "source" if self._view == "detail" else "detail"
        self._render_detail()

    def _render_detail(self) -> None:
        summary = self.query_one("#lib-summary", Static)
        entry = self._current()
        if entry is None:
            summary.update("No action selected.")
            return

        lines = [f"[b]{_escape(entry.name)}[/b]", ""]

        # The verdict leads. It is the conclusion the base-rate card exists to
        # produce, and burying it under metadata is how it went unread.
        if entry.verdict:
            lines += [f"[yellow]{_escape(entry.verdict)}[/yellow]", ""]

        if entry.description:
            lines += [_escape(entry.description), ""]
        lines += [
            f"source     {entry.source}",
            f"applies    {_escape(entry.applies_to)}",
            f"executes   {entry.executes}",
            f"effect     {'read-only' if entry.read_only else ', '.join(entry.writes)}",
        ]

        if entry.error:
            lines += ["", f"[red]unusable: {_escape(entry.error)}[/red]"]

        if entry.base_rate:
            bits = ", ".join(f"{k}={v}" for k, v in entry.base_rate.items() if k != "tsg")
            if bits:
                lines += ["", f"[dim]base rate: {bits}[/dim]"]

        if not entry.applies(self._incident):
            lines += ["", "[dim]Does not apply to the selected incident.[/dim]"]

        if self._view == "source" and entry.directory is not None:
            lines += ["", f"[dim]{entry.directory}[/dim]"]

        summary.update("\n".join(lines))

    # ------------------------------------------------------------------- run

    def action_open_source(self) -> None:
        entry = self._current()
        if entry and entry.directory is not None:
            webbrowser.open(entry.directory.as_uri())
        elif entry and entry.url:
            webbrowser.open(entry.url)

    def action_run(self) -> None:
        entry = self._current()
        if entry is None or entry.error:
            return

        if entry.source == "link":
            webbrowser.open(entry.url)
            return

        if self._incident is None:
            self._log("[yellow]Select an incident on the queue first; actions run against one.[/yellow]")
            return
        if not entry.applies(self._incident):
            self._log(
                f"[yellow]{entry.name} applies to {entry.applies_to}, "
                f"not to this incident.[/yellow]"
            )
            return
        if self._busy:
            self._log("[yellow]Something is already running.[/yellow]")
            return

        self._busy = True
        self._log(f"[b]running[/b] {entry.name} against {self._incident.incident_id} ...")
        self._execute(entry, self._incident)

    @work(thread=True, group="library")
    def _execute(self, entry: CatalogEntry, incident: Incident) -> None:
        try:
            if entry.source == "kusto" and entry.action is not None:
                from ..actions import run_action

                run = run_action(entry.action, incident, self._config)
                self.app.call_from_thread(self._show_kit_run, run)
            elif entry.skill is not None:
                from ..copilot import run_skill
                from ..packs import build_pack

                pack = build_pack(incident, self._config)
                run = run_skill(entry.skill, incident, pack, self._config, allow_shell=False)
                self.app.call_from_thread(self._show_skill_run, run)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim
            self.app.call_from_thread(self._log, f"[red]{entry.name} failed: {exc}[/red]")
        finally:
            self.app.call_from_thread(self._clear_busy)

    def _clear_busy(self) -> None:
        self._busy = False

    def _show_kit_run(self, run) -> None:
        colour = "green" if run.ok else "red"
        self._log(f"[{colour}]{run.summary()}[/{colour}]")
        if run.output_path:
            self._log(f"[dim]saved {run.output_path}[/dim]")
        for line in (run.stdout or "").strip().splitlines()[:40]:
            self._log(f"  {_escape(line)}")

    def _show_skill_run(self, run) -> None:
        colour = "green" if run.ok else "red"
        self._log(f"[{colour}]{run.summary()}[/{colour}]")
        for line in (run.answer or "(no answer)").strip().splitlines()[:40]:
            self._log(f"  {_escape(line)}")
        if run.resume_command:
            self._log(f"[dim]{run.resume_command}[/dim]")

    # ---------------------------------------------------------------- chrome

    def _log(self, message: str) -> None:
        self.query_one("#lib-output", RichLog).write(message)

    def action_close(self) -> None:
        self.dismiss()

    def _set_status(self, message: str) -> None:
        self.query_one("#lib-status", Static).update(message)


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")
