"""The Skill Browser.

Every individual skill ODSP maintains in Azure DevOps, plus the condition
specific Kusto kits and the incident's TSG link -- one row per thing you can
run on its own. Kits, which run several skills as a playbook, are a separate
screen.

This is the browse-and-pick surface: 50-odd skills is too many to scan under
pressure, so it carries a source filter and hides fleet-maintenance skills by
default.
"""

from __future__ import annotations

import webbrowser

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

from ..catalog import CatalogEntry, build_catalog, count_maintenance
from ..models import Incident


class LibraryScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("x", "run", "Run"),
        Binding("r", "refresh", "Refresh"),
        Binding("v", "toggle_view", "Detail / source"),
        Binding("o", "open_source", "Open folder"),
        Binding("a", "toggle_maintenance", "Show all"),
        Binding("slash", "filter", "Filter", key_display="/"),
        Binding("k", "show_kits", "Kits"),
    ]

    def __init__(self, config, tokens, incident: Incident | None = None) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._incident = incident
        self._entries: list[CatalogEntry] = []
        self._view = "detail"
        self._busy = False
        self._filter = ""
        self._show_maintenance = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="lib-body"):
            yield Input(placeholder="filter by name or source", id="lib-filter")
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
        # Hidden is not the same as inert. An Input that is merely undisplayed
        # still sits in the focus chain and swallows every keystroke, so `a`
        # and `/` were being typed into an invisible box instead of firing
        # their bindings. Disabling it takes it out of the chain entirely.
        self._hide_filter(initial=True)
        self.refresh_catalog()

    # ------------------------------------------------------------------ load

    def action_refresh(self) -> None:
        self.refresh_catalog()

    def refresh_catalog(self) -> None:
        self._entries = [
            entry
            for entry in build_catalog(
                self._config,
                self._incident,
                include_maintenance=self._show_maintenance,
            )
            if self._matches(entry)
        ]
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
        hidden = count_maintenance(self._config) if not self._show_maintenance else 0
        notes = []
        if hidden:
            notes.append(f"{hidden} maintenance hidden (a)")
        if self._filter:
            notes.append(f"filtered to {self._filter} (/)")
        suffix = " - " + " - ".join(notes) if notes else ""
        self._set_status(
            f"{len(self._entries)} action(s), {runnable} runnable here - {context}{suffix}"
        )
        self._render_detail()

    # ---------------------------------------------------------------- filter

    def _matches(self, entry: CatalogEntry) -> bool:
        """Substring match over the two fields an operator searches by.

        Name and source, not description: descriptions are long enough that
        matching them returns most of the list for most queries, which is the
        same as not filtering.
        """
        if not self._filter:
            return True
        needle = self._filter.lower()
        return needle in entry.name.lower() or needle in entry.source.lower()

    def action_filter(self) -> None:
        box = self.query_one("#lib-filter", Input)
        box.disabled = False
        box.display = True
        box.focus()

    def _hide_filter(self, initial: bool = False) -> None:
        box = self.query_one("#lib-filter", Input)
        box.value = ""
        box.display = False
        box.disabled = True
        self._filter = ""
        self.query_one("#lib-table", DataTable).focus()
        if not initial:
            self.refresh_catalog()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "lib-filter":
            self._filter = event.value.strip()
            self.refresh_catalog()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Return hands focus back to the table but keeps the filter applied."""
        if event.input.id == "lib-filter":
            self.query_one("#lib-table", DataTable).focus()

    def action_toggle_maintenance(self) -> None:
        self._show_maintenance = not self._show_maintenance
        self.refresh_catalog()

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

    def action_show_kits(self) -> None:
        from .kits_screen import KitsScreen

        self.app.push_screen(KitsScreen(self._config, self._tokens, self._incident))

    def on_key(self, event) -> None:
        """Escape leaves the filter box before it leaves the screen.

        Without this, escape while typing a filter dismisses the whole screen,
        which loses the operator's place for what reads like a corrective
        keystroke.
        """
        if event.key != "escape":
            return
        if not self.query_one("#lib-filter", Input).has_focus:
            return
        event.stop()
        event.prevent_default()
        self._hide_filter()

    def _set_status(self, message: str) -> None:
        self.query_one("#lib-status", Static).update(message)


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")

