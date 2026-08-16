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

import re
import webbrowser
from datetime import datetime, timezone

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

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


class IncidentScreen(Screen):
    """The incident queue.

    A Screen rather than the App itself so its bindings stay scoped to it. The
    footer merges App-level bindings into every screen, and advertising "Run"
    on the SLI view is worse than not advertising it at all.
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("p", "compose_payload", "Payload"),
        Binding("o", "open_icm", "IcM"),
        Binding("t", "open_tsg", "TSG"),
        Binding("s", "show_slis", "SLIs"),
        Binding("b", "show_bugs", "Bugs"),
        Binding("exclamation_mark", "show_settings", "Settings", key_display="!"),
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
        self._provenance = ""
        #: "detail" while browsing, "output" while an action is running.
        self._pane = "detail"
        self._output: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="incidents", cursor_type="row", zebra_stripes=True)
            # One pane, not a detail box above a log. The log held a startup
            # provenance line for the whole session and then sat empty, which
            # cost the bottom half of the side column permanently. It now
            # shows the selected incident, and takes over for a run's output.
            with VerticalScroll(id="side"):
                yield Static("Loading...", id="detail")
        yield Static("", id="status")
        yield Footer()

    #: Below this width the detail pane is dropped; see styles.tcss.
    NARROW = 100

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < self.NARROW, "-narrow")

    def on_mount(self) -> None:
        self.set_class(self.app.size.width < self.NARROW, "-narrow")
        table = self.query_one("#incidents", DataTable)
        # Deliberately few columns. Everything dropped here (owner, monitor,
        # track reason) is one keystroke away in the detail pane, and at a
        # typical terminal width each extra column is taken directly out of the
        # title -- which is the column an OCE actually reads.
        table.add_columns("SEV", "AGE", "FLAG", "ENV", "INCIDENT", "TITLE")
        self._kits = discover_kits(self._config)
        # Provenance belongs on the status line, not in a pane. It is set once
        # and never changes, so giving it half the side column for a whole
        # shift was the wrong trade.
        kits = f"{len(self._kits)} kit(s)" if self._kits else "no kits"
        self._provenance = f"{self._config.policy.label} - {kits}"
        # Housekeeping on the way in. Pruning ran only on the headless skill
        # path, so a console left open for weeks accumulated packs and saved
        # output indefinitely.
        self._prune_state()
        self.refresh_incidents()
        self.set_interval(self._config.intervals.get("incidents", 300), self.refresh_incidents)

    @work(thread=True, group="housekeeping")
    def _prune_state(self) -> None:
        from ..packs import prune_output, prune_packs

        try:
            prune_packs(self._config)
            prune_output(self._config)
        except Exception:  # noqa: BLE001 - housekeeping never blocks the queue
            pass

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
        self.app.call_from_thread(self._apply_incidents, generation, result)

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
        self._restore_status(result)

    def _restore_status(self, result: SourceResult) -> None:
        """The queue's own status line.

        Extracted so a finished run can put it back rather than leaving
        "running ..." on screen until the next refresh.
        """
        detail = result.detail
        # "27 open (30 already mitigated)" rather than "57 in scope": an OCE
        # reads the latter as "30 are being hidden from me".
        closed = max(detail["rows_returned"] - len(result.data), 0)
        self._set_status(
            f"{len(result.data)} open  ({closed} already mitigated) - "
            f"{result.age_label()} - {detail['duration_ms']}ms"
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
                _env(incident),
                incident.incident_id,
                incident.title[:120],
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
        # Moving the cursor is how you get back from a finished run's output.
        self._pane = "detail"
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
            # "how long" and "since when" are different questions, and an
            # on-call engineer picking up a queue asks both.
            f"opened   {incident.opened_at or '(unknown)'}",
            f"open     {incident.hours_open:.0f}h" + ("  [red]STALE[/red]" if incident.is_stale else ""),
        ]
        if incident.is_customer_impacting:
            lines.append("[yellow]customer impacting[/yellow]")
        if incident.runs_tracked is not None:
            lines.append(f"tracked  the fleet has looked at this {incident.runs_tracked} time(s)")

        lines.append("")
        # The full path lives on the compose screen, which has the width for
        # it. Here, in a 35-column pane, an absolute path wraps into noise --
        # what an operator needs from the queue is whether one exists.
        from ..payload import payload_path

        path = payload_path(incident, self._config)
        state = "[green]written[/green]" if path.is_file() else "[dim]none yet[/dim]"
        lines.append(f"[b]p[/b]  compose a payload for your agent   {state}")

        if not self._candidates:
            lines += ["", "[dim]No investigation query matches this monitor.[/dim]"]
            if not incident.monitor_id:
                lines.append("[dim]It carries no monitorId, so none can be matched.[/dim]")
        else:
            action = self._candidates[0]
            if action.kind == "kit":
                lines += ["", f"[dim]query available: {_escape(action.id)}[/dim]"]
                if action.base_rate:
                    bits = ", ".join(
                        f"{k}={v}" for k, v in action.base_rate.items() if k != "tsg"
                    )
                    if bits:
                        lines.append(f"[dim]base rate: {bits}[/dim]")

        # Last, because it is the longest thing here and the fields above are
        # what an operator triages on. Plenty of monitor-filed incidents carry
        # nothing, so its absence is stated rather than left as a blank gap.
        lines += ["", "[b]Description[/b]"]
        description = incident.description
        if description:
            lines += [_escape(line) for line in description.splitlines()]
        else:
            lines.append("[dim]IcM recorded no description for this incident.[/dim]")

        detail.update("\n".join(lines))

    # ---------------------------------------------------------------- actions

    def action_show_settings(self) -> None:
        """Connectors, permissions, and where the configuration came from."""
        from .settings_screen import SettingsScreen

        self.app.push_screen(SettingsScreen(self._config, self._tokens))

    def action_show_slis(self) -> None:
        """Open the SLI view.

        A separate screen because it answers a different question from the
        queue: not "what is broken" but "is the service meeting its objective".
        """
        from .sli_screen import SliScreen

        self.app.push_screen(SliScreen(self._config, self._tokens))

    def action_show_kits(self) -> None:
        """Kits: playbooks that run several skills against the selected incident."""
        from .kits_screen import KitsScreen

        self.app.push_screen(
            KitsScreen(self._config, self._tokens, self._current_incident())
        )

    def action_show_skills(self) -> None:
        """The skill browser: every individual action, run one at a time."""
        from .library_screen import LibraryScreen

        self.app.push_screen(
            LibraryScreen(self._config, self._tokens, self._current_incident())
        )

    def action_show_bugs(self) -> None:
        """The bug tracker, which is also where new bugs are filed.

        Filing used to be a top-level key beside Refresh and Quit, which put a
        rarely-used write action in the operator's way on every screen. It
        belongs next to the list of what is already open.
        """
        from .bug_screen import BugScreen

        self.app.push_screen(
            BugScreen(self._config, self._tokens, self._current_incident())
        )

    def action_compose_payload(self) -> None:
        """Assemble a handoff for this incident."""
        incident = self._current_incident()
        if incident is None:
            return
        from .compose_screen import ComposeScreen

        def _done(_: object | None) -> None:
            self._update_detail()

        self.app.push_screen(ComposeScreen(self._config, self._tokens, incident), _done)

    def action_open_icm(self) -> None:
        incident = self._current_incident()
        if incident:
            webbrowser.open(incident.icm_url)

    def action_open_tsg(self) -> None:
        incident = self._current_incident()
        # IcM stores "** REDACTED **" for MSRC incidents, which was being
        # handed to the browser as a URL. A field that is not a link is not a
        # link, whatever it says.
        if incident and incident.tsg_id.lower().startswith("http"):
            webbrowser.open(incident.tsg_id)
        elif incident and incident.tsg_id:
            self._log(f"[yellow]IcM recorded no usable TSG: {_escape(incident.tsg_id)}[/yellow]")
        else:
            self._log("[dim]no TSG recorded on this incident[/dim]")

    def action_run_action(self) -> None:
        incident = self._current_incident()
        if incident is None or not self._candidates:
            return
        if self._busy:
            self._log("[yellow]an action is already running[/yellow]")
            return

        action = self._candidates[0]
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
                self._set_running(f"running {action.id} against {incident.incident_id} ...")
                self._log(f"[b]running[/b] {action.id} against {incident.incident_id} ...")
                self._log("[dim]querying Kusto; the result opens when it returns[/dim]")
                self._execute(action, incident)

        self.app.push_screen(ConfirmRun(action, incident, command), _decide)

    @work(thread=True, group="actions")
    def _execute(self, action: Action, incident: Incident) -> None:
        try:
            run = run_action(action, incident, self._config)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
            self.app.call_from_thread(self._log, f"[red]{action.id} could not start: {exc}[/red]")
            self.app.call_from_thread(self._clear_busy)
            return
        self.app.call_from_thread(self._show_run, run)
        self.app.call_from_thread(self._clear_busy)

    def _clear_busy(self) -> None:
        self._busy = False
        try:
            self.query_one("#side").loading = False
        except Exception:  # noqa: BLE001 - during teardown the widget may be gone
            pass
        if self._last_result is not None:
            self._restore_status(self._last_result)

    def _set_running(self, label: str) -> None:
        """Visible proof that a keypress did something.

        A Kusto query takes a few seconds, and a console that looks identical
        while it works is a console an operator presses twice.
        """
        self._set_status(label)
        try:
            self.query_one("#side").loading = True
        except Exception:  # noqa: BLE001
            pass

    def _show_run(self, run: ActionRun) -> None:
        """Open the result full width rather than wrapping it into the pane.

        A kit's output is a table 150 columns wide; the side pane is about 35.
        Writing one into the other wrapped every row four times, which is what
        made results unreadable.
        """
        from ..dataexplorer import kit_url
        from .result_screen import ResultScreen

        note = ""
        if run.artifacts:
            note = f"the kit also wrote beside itself: {', '.join(run.artifacts)}"

        body = run.stdout.rstrip() or "(no output)"
        if run.stderr.strip():
            body = f"{body}\n\n--- stderr ---\n{run.stderr.rstrip()}"

        action = next((a for a in self._candidates if a.id == run.action_id), None)
        explorer = kit_url(action.directory if action else None, run.incident_id)

        self._log(f"[{'green' if run.ok else 'red'}]{run.action_id}: {run.summary()}[/]")
        self.app.push_screen(
            ResultScreen(
                title=run.action_id,
                summary=f"{run.summary()}  -  {_row_count(run.stdout)}",
                body=body,
                output_path=run.output_path,
                ok=run.ok,
                note=note,
                explorer_url=explorer,
            )
        )
        if run.stderr.strip():
            for line in run.stderr.strip().splitlines()[:20]:
                self._log(f"  [red]{_escape(line)}[/red]")

    # ----------------------------------------------------------------- chrome

    def _log(self, message: str) -> None:
        """Append to the run output and show it in the side pane."""
        self._output.append(message)
        self._pane = "output"
        self._render_pane()

    def _render_pane(self) -> None:
        if self._pane == "output":
            body = "\n".join(self._output[-400:]) or "(no output)"
            hint = "\n\n[dim]Move the cursor to go back to the incident.[/dim]"
            self.query_one("#detail", Static).update(body + hint)
        else:
            self._update_detail()

    def _set_status(self, message: str) -> None:
        """The status line also carries provenance.

        Which policy and how many kits are loaded never changes during a
        session, so it belongs on a line that is always there rather than in a
        pane that could be showing something useful.
        """
        suffix = f"     [dim]{self._provenance}[/dim]" if self._provenance else ""
        self.query_one("#status", Static).update(f"{message}{suffix}")


def _age(incident: Incident) -> str:
    hours = incident.hours_open
    return f"{hours:.0f}h" if hours < 100 else f"{hours / 24:.0f}d"


#: Four characters is enough to tell the rings apart, and the full value is in
#: the detail pane.
_ENV_SHORT = {"UNCLASSIFIED": "UNCL", "PATHFINDER": "PATH", "TRAILBLAZER": "TRLB"}


def _env(incident: Incident) -> str:
    env = incident.env_class or "-"
    return _ENV_SHORT.get(env, env[:4])


def _flag(incident: Incident) -> str:
    if incident.is_customer_impacting:
        return "CUST"
    if incident.is_stale:
        return "STALE"
    return ""


def _row_count(stdout: str) -> str:
    """The kit states its own row count; surface it in the header.

    "125 row(s)" is the first thing an operator wants from a query result and
    it would otherwise be six lines down.
    """
    match = re.search(r"^(\d+)\s+row\(s\)", stdout or "", re.M)
    return f"{match.group(1)} rows" if match else "no row count reported"


def _escape(text: str) -> str:
    """Incident and kit text is data, not markup. Square brackets are common."""
    return str(text).replace("[", r"\[")


class OceSentryApp(App):
    """Thin shell.

    Every view is a Screen so the footer only ever advertises keys that work
    where you are standing.
    """

    CSS_PATH = "styles.tcss"
    TITLE = "OCE Sentry"

    def __init__(self, config: Config, tokens: TokenProvider) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens

    def on_mount(self) -> None:
        self.push_screen(IncidentScreen(self._config, self._tokens))


def run_app(config: Config, tokens: TokenProvider) -> int:
    OceSentryApp(config, tokens).run()
    return 0











