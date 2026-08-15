"""The terminal UI.

Design notes worth keeping in mind when editing:

* Every fetch and every action runs on a thread worker. A Kusto query takes
  seconds and a kit can take minutes; doing either on the event loop freezes the
  UI at exactly the moment an OCE is trying to read it.
* Fetches carry a generation id. Two refreshes in flight can complete out of
  order, and an older result overwriting a newer one is a silent correctness bug.
* Nothing executes without a confirmation that shows the resolved argv.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime, timezone

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Label, RichLog, Static

from ..actions import Action, ActionRun, actions_for, build_command, discover_kits, run_action
from ..auth import AuthError, TokenProvider
from ..config import Config
from ..kusto import KustoClient
from ..models import Incident, SourceResult
from ..sources.incidents import fetch_incidents


class ConfirmRun(ModalScreen[bool]):
    """Explicit confirmation, showing exactly what will run.

    The resolved argument vector is displayed rather than a friendly summary:
    the operator is authorising a command against production as themselves, and
    they should see the command.
    """

    BINDINGS = [
        Binding("y", "confirm", "Run"),
        Binding("escape,n", "cancel", "Cancel"),
    ]

    def __init__(self, action: Action, incident: Incident, command: list[str]) -> None:
        super().__init__()
        self._action = action
        self._incident = incident
        self._command = command

    def compose(self) -> ComposeResult:
        effects = (
            "read-only"
            if self._action.read_only
            else "WRITES: " + ", ".join(self._action.writes)
        )
        rendered = "\n  ".join(self._command)
        with Vertical(id="confirm-box"):
            yield Label(f"Run {self._action.id} against incident {self._incident.incident_id}?")
            yield Static(
                "\nThis executes locally as you, against production data.\n"
                f"Declared side effects: {effects}\n\n"
                f"Command:\n  {rendered}\n",
                id="confirm-detail",
            )
            yield Label("y = run     esc = cancel")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class OceSentryApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "OCE Sentry"

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_icm", "Open IcM"),
        Binding("t", "open_tsg", "Open TSG"),
        Binding("x", "run_action", "Run action"),
        Binding("bracketright", "next_action", "Next action"),
        Binding("bracketleft", "prev_action", "Prev action"),
        Binding("s", "show_slis", "SLIs"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config, tokens: TokenProvider) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._client = KustoClient(tokens, timeout=config.query_timeout)
        self._incidents: list[Incident] = []
        self._kits: list[Action] = []
        self._candidates: list[Action] = []
        self._selected_action = 0
        self._generation = 0
        self._last_result: SourceResult | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="incidents", cursor_type="row", zebra_stripes=True)
            with Vertical(id="side"):
                yield Static("Loading...", id="detail")
                yield RichLog(id="log", wrap=True, markup=True, highlight=False)
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#incidents", DataTable)
        table.add_columns("SEV", "AGE", "FLAG", "ENV", "OWNER", "INCIDENT", "TITLE")
        self._kits = discover_kits(self._config)
        self._log(
            f"[dim]policy {self._config.policy.label} - "
            f"{len(self._kits)} kit(s) - output {self._config.output_dir}[/dim]"
        )
        self.refresh_incidents()
        self.set_interval(self._config.intervals.get("incidents", 300), self.refresh_incidents)

    # ------------------------------------------------------------------ fetch

    def action_refresh(self) -> None:
        self.refresh_incidents()

    def refresh_incidents(self) -> None:
        self._generation += 1
        self._set_status(f"refreshing... (policy {self._config.policy.label})")
        self._fetch(self._generation)

    @work(thread=True, exclusive=True, group="fetch")
    def _fetch(self, generation: int) -> None:
        try:
            result = fetch_incidents(self._config, self._client)
        except AuthError as exc:  # pragma: no cover - depends on live auth
            result = SourceResult(
                name="incidents",
                data=[],
                fetched_at=datetime.now(timezone.utc),
                error=str(exc),
            )
        self.call_from_thread(self._apply_incidents, generation, result)

    def _apply_incidents(self, generation: int, result: SourceResult) -> None:
        # Discard a completion that a newer refresh has already superseded.
        if generation != self._generation:
            return

        if not result.ok:
            # Keep whatever we last had rather than blanking the queue, and say
            # plainly that it is now stale.
            if self._last_result is not None:
                self._last_result.stale = True
                self._set_status(f"[red]{result.error}[/red] - showing last known data")
            else:
                self._set_status(f"[red]{result.error}[/red]")
            self._log(f"[red]incidents: {result.error}[/red]")
            return

        self._last_result = result
        self._incidents = result.data
        self._render_table()
        detail = result.detail
        self._set_status(
            f"{len(result.data)} open - {detail['rows_returned']} in scope - "
            f"{detail['duration_ms']}ms - {result.age_label()} - policy {self._config.policy.label}"
        )

    def _render_table(self) -> None:
        table = self.query_one("#incidents", DataTable)
        previous = self._current_incident()
        table.clear()
        for incident in self._incidents:
            table.add_row(
                incident.severity_label,
                _age(incident),
                _flag(incident),
                incident.env_class[:12],
                (incident.owning_contact_alias or "-")[:14],
                incident.incident_id,
                incident.title[:90],
            )
        # Preserve the operator's place across a refresh.
        if previous is not None:
            for index, incident in enumerate(self._incidents):
                if incident.incident_id == previous.incident_id:
                    table.move_cursor(row=index)
                    break
        self._update_detail()

    # ----------------------------------------------------------------- detail

    def _current_incident(self) -> Incident | None:
        try:
            table = self.query_one("#incidents", DataTable)
        except Exception:  # noqa: BLE001 - during teardown the widget may be gone
            return None
        row = table.cursor_row
        if not self._incidents or row is None or row < 0 or row >= len(self._incidents):
            return None
        return self._incidents[row]

    @on(DataTable.RowHighlighted)
    def _on_row(self) -> None:
        self._selected_action = 0
        self._update_detail()

    def _update_detail(self) -> None:
        detail = self.query_one("#detail", Static)
        incident = self._current_incident()
        if incident is None:
            detail.update("No incident selected.")
            self._candidates = []
            return

        self._candidates = actions_for(incident, self._kits)
        lines = [
            f"[b]{incident.incident_id}[/b]  Sev {incident.severity_label}  "
            f"{incident.status}  {incident.env_class}",
            _escape(incident.title),
            "",
            f"owner    {incident.owning_contact_alias or '(unassigned)'}  ({incident.owning_team_name})",
            f"monitor  {_escape(incident.monitor_id) if incident.monitor_id else '(none recorded)'}",
            f"reason   {incident.track_reason}",
            f"open     {incident.hours_open:.0f}h" + ("  [red]STALE[/red]" if incident.is_stale else ""),
        ]
        if incident.is_customer_impacting:
            lines.append("[yellow]customer impacting[/yellow]")
        if incident.runs_tracked is not None:
            lines.append(f"tracked  the fleet has looked at this {incident.runs_tracked} time(s)")

        lines.append("")
        if not self._candidates:
            lines.append("[dim]No runbook matches this incident.[/dim]")
            if not incident.monitor_id:
                lines.append("[dim]It carries no monitorId, so kits cannot be matched.[/dim]")
        else:
            lines.append("[b]Actions[/b]  ( [ and ] to choose, x to run )")
            for index, action in enumerate(self._candidates):
                marker = ">" if index == self._selected_action else " "
                effects = "read-only" if action.read_only else "writes"
                lines.append(f"{marker} [{action.kind}] {_escape(action.id)}  [dim]{effects}[/dim]")
                if index == self._selected_action and action.base_rate:
                    bits = ", ".join(f"{k}={v}" for k, v in action.base_rate.items() if k != "tsg")
                    if bits:
                        lines.append(f"    [dim]base rate: {bits}[/dim]")
        detail.update("\n".join(lines))

    # ---------------------------------------------------------------- actions

    def action_next_action(self) -> None:
        if self._candidates:
            self._selected_action = (self._selected_action + 1) % len(self._candidates)
            self._update_detail()

    def action_prev_action(self) -> None:
        if self._candidates:
            self._selected_action = (self._selected_action - 1) % len(self._candidates)
            self._update_detail()

    def action_show_slis(self) -> None:
        """Open the SLI view.

        A separate screen because it answers a different question from the
        queue: not "what is broken" but "is the service meeting its objective".
        """
        from .sli_screen import SliScreen

        self.push_screen(SliScreen(self._config, self._tokens))

    def action_open_icm(self) -> None:
        incident = self._current_incident()
        if incident:
            webbrowser.open(incident.icm_url)

    def action_open_tsg(self) -> None:
        incident = self._current_incident()
        if incident and incident.tsg_id:
            webbrowser.open(incident.tsg_id)
        else:
            self._log("[dim]no TSG recorded on this incident[/dim]")

    def action_run_action(self) -> None:
        incident = self._current_incident()
        if incident is None or not self._candidates:
            return
        if self._busy:
            self._log("[yellow]an action is already running[/yellow]")
            return

        action = self._candidates[self._selected_action]
        if action.kind == "link":
            webbrowser.open(action.url)
            return

        try:
            command = build_command(action, incident)
        except (ValueError, FileNotFoundError) as exc:
            self._log(f"[red]{exc}[/red]")
            return

        def _decide(confirmed: bool | None) -> None:
            if confirmed:
                self._busy = True
                self._log(f"[b]running[/b] {action.id} against {incident.incident_id} ...")
                self._execute(action, incident)

        self.push_screen(ConfirmRun(action, incident, command), _decide)

    @work(thread=True, group="actions")
    def _execute(self, action: Action, incident: Incident) -> None:
        try:
            run = run_action(action, incident, self._config)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
            self.call_from_thread(self._log, f"[red]{action.id} could not start: {exc}[/red]")
            self.call_from_thread(self._clear_busy)
            return
        self.call_from_thread(self._show_run, run)
        self.call_from_thread(self._clear_busy)

    def _clear_busy(self) -> None:
        self._busy = False

    def _show_run(self, run: ActionRun) -> None:
        colour = "green" if run.ok else "red"
        self._log(f"[{colour}]{run.action_id}: {run.summary()}[/{colour}]")
        if run.output_path:
            self._log(f"[dim]saved {run.output_path}[/dim]")
        if run.artifacts:
            self._log(
                f"[yellow]the kit also wrote beside itself: {', '.join(run.artifacts)}[/yellow]"
            )
        body = run.stdout.strip() or "(no stdout)"
        for line in body.splitlines()[:60]:
            self._log(f"  {_escape(line)}")
        if run.stderr.strip():
            for line in run.stderr.strip().splitlines()[:20]:
                self._log(f"  [red]{_escape(line)}[/red]")

    # ----------------------------------------------------------------- chrome

    def _log(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)


def _age(incident: Incident) -> str:
    hours = incident.hours_open
    return f"{hours:.0f}h" if hours < 100 else f"{hours / 24:.0f}d"


def _flag(incident: Incident) -> str:
    if incident.is_customer_impacting:
        return "CUST"
    if incident.is_stale:
        return "STALE"
    return ""


def _escape(text: str) -> str:
    """Incident and kit text is data, not markup. Square brackets are common."""
    return str(text).replace("[", r"\[")


def run_app(config: Config, tokens: TokenProvider) -> int:
    OceSentryApp(config, tokens).run()
    return 0


