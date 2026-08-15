"""The Kits view.

A kit is a named set of skills run in order against the selected incident. The
screen is organised around the question each kit answers, because that is what
an on-call engineer is holding when they open it -- not a skill name.

Running a kit starts one Copilot session per skill, sequentially, and streams
each result as it lands. Sequential rather than parallel is deliberate: the
output is meant to be read while it arrives, and four answers appearing at once
is the same as none.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Label, Static

from ..kits import Kit, KitStep, load_kits, run_kit
from ..models import Incident


class ConfirmKit(ModalScreen[bool]):
    """Confirmation before a kit runs.

    A kit is several model sessions against production evidence, which costs
    time and credits and is not something to trigger by leaning on a key. The
    skills are listed by name so the operator authorises what actually runs.
    """

    BINDINGS = [
        Binding("y", "confirm", "Run"),
        Binding("escape,n", "cancel", "Cancel"),
    ]

    def __init__(self, kit: Kit, incident: Incident) -> None:
        super().__init__()
        self._kit = kit
        self._incident = incident

    def compose(self) -> ComposeResult:
        steps = "\n  ".join(f"{i}. {s.id}" for i, s in enumerate(self._kit.skills, 1))
        missing = ""
        if self._kit.missing:
            missing = (
                "\nNot installed, will be skipped:\n  "
                + ", ".join(self._kit.missing)
                + "\n"
            )
        with Vertical(id="confirm-box"):
            yield Label(f"Run {self._kit.name} against incident {self._incident.incident_id}?")
            yield Static(
                f"\n{self._kit.question}\n\n"
                f"This starts {len(self._kit.skills)} Copilot session(s), one per skill,\n"
                "locally as you, with shell access denied.\n\n"
                f"Runs in order:\n  {steps}\n{missing}",
                id="confirm-detail",
            )
            yield Label("y = run     esc = cancel")
        yield Footer()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class KitsScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("x", "run", "Run kit"),
        Binding("c", "cancel_run", "Stop"),
        Binding("l", "show_skills", "Skill browser"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, config, tokens, incident: Incident | None = None) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._incident = incident
        self._kits: list[Kit] = []
        self._busy = False
        self._cancel = False
        self._pane = "detail"
        self._output: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="kit-body"):
            yield DataTable(id="kit-table", cursor_type="row", zebra_stripes=True)
            # One pane, same as the skill browser: the kit's steps while
            # browsing, the run's output while it is running.
            with VerticalScroll(id="kit-detail"):
                yield Static("", id="kit-summary")
        yield Static("", id="kit-status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#kit-table", DataTable).add_columns(
            "KIT", "ANSWERS", "SKILLS", "STATUS"
        )
        self.refresh_kits()

    # ------------------------------------------------------------------ load

    def action_refresh(self) -> None:
        self.refresh_kits()

    def refresh_kits(self) -> None:
        self._kits = load_kits(self._config)
        table = self.query_one("#kit-table", DataTable)
        table.clear()

        for kit in self._kits:
            if not kit.available:
                name = f"[red]{kit.name}[/red]"
                status = "[red]none installed[/red]"
            elif kit.missing:
                name = kit.name
                status = f"[yellow]{kit.coverage}[/yellow]"
            else:
                name = kit.name
                status = "[green]ready[/green]"
            table.add_row(name, kit.question[:52], kit.coverage, status)

        ready = sum(1 for k in self._kits if k.available)
        if self._incident is not None:
            context = f"incident {self._incident.incident_id}"
        else:
            context = "no incident selected - pick one on the queue before running"
        self._set_status(f"{len(self._kits)} kit(s), {ready} runnable - {context}")
        # Respect the pane state so a refresh cannot wipe a run's output.
        self._render_pane()

    # ---------------------------------------------------------------- detail

    def _current(self) -> Kit | None:
        table = self.query_one("#kit-table", DataTable)
        row = table.cursor_row
        if not self._kits or row is None or row < 0 or row >= len(self._kits):
            return None
        return self._kits[row]

    def on_data_table_row_highlighted(self) -> None:
        self._pane = "detail"
        self._render_detail()

    def _render_detail(self) -> None:
        summary = self.query_one("#kit-summary", Static)
        kit = self._current()
        if kit is None:
            summary.update("No kit selected.")
            return

        lines = [
            f"[b]{_escape(kit.name)}[/b]",
            "",
            f"[yellow]{_escape(kit.question)}[/yellow]",
            "",
            _escape(kit.when),
            "",
            "[b]Runs in order[/b]",
        ]
        installed = {s.id: s for s in kit.skills}
        for index, skill_id in enumerate(kit.skill_ids, 1):
            skill = installed.get(skill_id)
            if skill is None:
                lines.append(f"  {index}. [red]{_escape(skill_id)} - not installed[/red]")
            else:
                lines.append(f"  {index}. {_escape(skill.id)}")
                if skill.description:
                    lines.append(f"     [dim]{_escape(skill.description[:88])}[/dim]")

        lines += [
            "",
            f"effect     read-only, shell denied",
            f"sessions   {len(kit.skills)} Copilot run(s), one per skill",
        ]
        if kit.missing:
            lines += [
                "",
                "[yellow]Missing skills are skipped, not substituted. Clone the",
                "source repository and set OCE_SENTRY_SKILLS to complete this kit.[/yellow]",
            ]
        summary.update("\n".join(lines))

    # ------------------------------------------------------------------- run

    def action_run(self) -> None:
        kit = self._current()
        if kit is None:
            return
        if not kit.available:
            self._log(
                f"[yellow]{kit.name} has none of its skills installed "
                f"({', '.join(kit.missing)}).[/yellow]"
            )
            return
        if self._incident is None:
            self._log("[yellow]Select an incident on the queue first; kits run against one.[/yellow]")
            return
        if self._busy:
            self._log("[yellow]A kit is already running. Press c to stop it.[/yellow]")
            return

        def _decide(confirmed: bool | None) -> None:
            if confirmed:
                self._start(kit, self._incident)

        self.app.push_screen(ConfirmKit(kit, self._incident), _decide)

    def _start(self, kit: Kit, incident: Incident) -> None:
        self._busy = True
        self._cancel = False
        self._log("")
        self._log(f"[b]{_escape(kit.name)}[/b] against {incident.incident_id}")
        self._log(f"[dim]{len(kit.skills)} skill(s), sequential, shell denied[/dim]")
        self._execute(kit, incident)

    def action_cancel_run(self) -> None:
        """Stop after the running skill finishes.

        A Copilot session is not safely killable mid-flight, so this asks the
        kit to stop rather than claiming to have stopped it, and says so.
        """
        if not self._busy:
            return
        self._cancel = True
        self._log("[yellow]Stopping after the current skill finishes.[/yellow]")

    @work(thread=True, group="kits")
    def _execute(self, kit: Kit, incident: Incident) -> None:
        def on_event(phase: str, skill_id: str, step: KitStep | None) -> None:
            if phase == "start":
                self.app.call_from_thread(self._log, f"[dim]-> {skill_id} ...[/dim]")
            elif step is not None:
                self.app.call_from_thread(self._show_step, step)

        try:
            run = run_kit(
                kit,
                incident,
                self._config,
                on_event=on_event,
                should_cancel=lambda: self._cancel,
            )
            self.app.call_from_thread(self._show_summary, run)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim
            self.app.call_from_thread(self._log, f"[red]{kit.name} failed: {exc}[/red]")
        finally:
            self.app.call_from_thread(self._clear_busy)

    def _clear_busy(self) -> None:
        self._busy = False

    def _show_step(self, step: KitStep) -> None:
        if step.skipped:
            self._log(f"[dim]{step.skill_id} skipped[/dim]")
            return
        if step.ok:
            self._log(f"[green]{step.skill_id}[/green] [dim]{step.duration_ms / 1000:.0f}s[/dim]")
            for line in (step.answer or "(no answer)").splitlines()[:24]:
                self._log(f"  {_escape(line)}")
        else:
            self._log(f"[red]{step.skill_id} failed: {_escape(step.error)}[/red]")
        if step.resume_command:
            self._log(f"[dim]  {step.resume_command}[/dim]")

    def _show_summary(self, run) -> None:
        colour = "green" if run.ok else "yellow"
        self._log(f"[{colour}]{_escape(run.summary())}[/{colour}]")

    # ---------------------------------------------------------------- chrome

    def action_show_skills(self) -> None:
        from .library_screen import LibraryScreen

        self.app.push_screen(LibraryScreen(self._config, self._tokens, self._incident))

    def _log(self, message: str) -> None:
        """Append to the run output and show it.

        A kit run streams several skills' answers, which is exactly when the
        operator wants the whole pane rather than half of it.
        """
        self._output.append(message)
        self._pane = "output"
        self._render_pane()

    def _render_pane(self) -> None:
        if self._pane == "output":
            body = "\n".join(self._output[-600:]) or "(no output)"
            hint = "\n\n[dim]Move the cursor to go back to the kit detail.[/dim]"
            self.query_one("#kit-summary", Static).update(body + hint)
        else:
            self._render_detail()

    def action_close(self) -> None:
        self.dismiss()

    def _set_status(self, message: str) -> None:
        self.query_one("#kit-status", Static).update(message)


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")
