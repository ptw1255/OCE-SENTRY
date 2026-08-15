"""Settings: connectors, permissions, and where everything came from.

One screen that answers "why can't this skill see live data", which previously
required reading source. It carries the connector inventory, the two
permissions that change what an action can reach, and the resolved paths and
policy behind the rest of the console.

Nothing here is edited in place. Every setting is an environment variable, and
the screen shows the exact command to change it -- a console that executes
production actions should not also be quietly rewriting its own permissions.
"""

from __future__ import annotations

import webbrowser

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from ..connectors import (
    Connector,
    annotate_requirements,
    config_path,
    load_connectors,
    mcp_enabled,
    probe,
    status_summary,
)
from ..copilot import shell_escalation_enabled
from ..dataplanes import DataPlane, discover_planes, plane_summary, probe_plane


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("r", "refresh", "Re-probe"),
        Binding("p", "probe_planes", "Probe clusters"),
        Binding("v", "toggle_view", "Servers / clusters"),
        Binding("o", "open_source", "Open config"),
        Binding("c", "copy_hint", "How to change"),
    ]

    def __init__(self, config, tokens) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._connectors: list[Connector] = []
        self._planes: list[DataPlane] = []
        self._view = "servers"
        self._probing = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="set-body"):
            yield Static("", id="set-summary")
            yield DataTable(id="set-table", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="set-detail"):
                yield Static("", id="set-info")
                yield RichLog(id="set-log", wrap=True, markup=True, highlight=False)
        yield Static("", id="set-status")
        yield Footer()

    def on_mount(self) -> None:
        self._build_columns()
        self._render_summary()
        self._load_planes()
        self.action_refresh()

    def _build_columns(self) -> None:
        table = self.query_one("#set-table", DataTable)
        table.clear(columns=True)
        if self._view == "servers":
            table.add_columns("CONNECTOR", "STATUS", "KIND", "PURPOSE", "NEEDED BY")
        else:
            table.add_columns("CLUSTER", "DATABASE", "STATUS", "USED BY", "PURPOSE", "SKILLS")

    def action_toggle_view(self) -> None:
        """MCP servers and Kusto clusters are different questions.

        One table with a mixed meaning of "status" would answer neither: a
        server is ready when its command exists, a cluster when it accepts a
        query as this operator.
        """
        self._view = "clusters" if self._view == "servers" else "servers"
        self._build_columns()
        if self._view == "servers":
            self._show(self._connectors)
        else:
            self._show_planes()

    def _load_planes(self) -> None:
        from ..skills import discover_skills

        skills = [s for s in discover_skills(self._config) if s.ok]
        self._planes = discover_planes(self._config, skills)

    def _show_planes(self) -> None:
        if self._view != "clusters":
            return
        table = self.query_one("#set-table", DataTable)
        row = table.cursor_row
        table.clear()
        for plane in self._planes:
            table.add_row(
                plane.host[:44],
                (plane.database or "-")[:16],
                _plane_status(plane),
                plane.used_by,
                (plane.purpose or "")[:40],
                str(len(plane.required_by) or "-"),
            )
        if row is not None and 0 <= row < len(self._planes):
            table.move_cursor(row=row)
        self._render_detail()

    def action_probe_planes(self) -> None:
        """Ask each cluster a trivial question, as this operator.

        On demand rather than on open: each cluster needs its own token, so
        probing a dozen is slow enough that doing it automatically would make
        Settings feel broken.
        """
        if self._view != "clusters":
            self._view = "clusters"
            self._build_columns()
            self._show_planes()
        if self._probing:
            return
        self._probing = True
        self._log("[b]probing clusters[/b] [dim]print ProbeOk=1, reads nothing[/dim]")
        self._set_status("probing clusters ...")
        self._probe_planes()

    @work(thread=True, group="planes")
    def _probe_planes(self) -> None:
        try:
            for plane in self._planes:
                probe_plane(plane, self._tokens)
                self.app.call_from_thread(self._show_planes)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim
            self.app.call_from_thread(self._log, f"[red]cluster probe failed: {exc}[/red]")
        finally:
            self.app.call_from_thread(self._done_planes)

    def _done_planes(self) -> None:
        self._probing = False
        self._set_status(plane_summary(self._planes))
        denied = [p for p in self._planes if p.status == "denied"]
        if denied:
            self._log(
                f"[yellow]{len(denied)} cluster(s) refused this identity: "
                f"{', '.join(p.host.split('.')[0] for p in denied)}[/yellow]"
            )
            self._log("[dim]Skills needing them will fall back to the evidence pack.[/dim]")

    # --------------------------------------------------------------- summary

    def _render_summary(self) -> None:
        path = config_path(self._config)
        wired = mcp_enabled()

        if not wired:
            headline = (
                "[yellow]Connectors are NOT wired into skill runs.[/yellow] "
                "Skills can only read the evidence pack."
            )
        elif path is None:
            headline = "[red]Connectors are enabled but no MCP config was found.[/red]"
        else:
            headline = "[green]Connectors are wired into skill runs.[/green]"

        shell = (
            "[red]shell escalation ENABLED[/red]"
            if shell_escalation_enabled()
            else "shell denied to skills"
        )

        self.query_one("#set-summary", Static).update(
            f"{headline}\n"
            f"config     {path or 'none found'}\n"
            f"policy     {self._config.policy.label}     {shell}"
        )

    # --------------------------------------------------------------- probing

    def action_refresh(self) -> None:
        if self._probing:
            return
        self._probing = True
        self._set_status("probing connectors ...")
        self._probe_all()

    @work(thread=True, group="connectors")
    def _probe_all(self) -> None:
        from ..skills import discover_skills

        try:
            connectors = load_connectors(self._config)
            skills = [s for s in discover_skills(self._config) if s.ok]
            annotate_requirements(connectors, skills)
            for connector in connectors:
                probe(connector)
                # Publish as each lands: probing twelve servers takes seconds
                # and a frozen table is indistinguishable from a hang.
                self.app.call_from_thread(self._show, list(connectors))
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim
            self.app.call_from_thread(self._log, f"[red]probe failed: {exc}[/red]")
        finally:
            self.app.call_from_thread(self._done_probing)

    def _done_probing(self) -> None:
        self._probing = False
        self._set_status(
            f"{status_summary(self._connectors)} - "
            f"{len(self._planes)} kusto cluster(s), press v - "
            f"{'wired into skill runs' if mcp_enabled() else 'not wired (see c)'}"
        )

    def _show(self, connectors: list[Connector]) -> None:
        self._connectors = connectors
        if self._view != "servers":
            return
        table = self.query_one("#set-table", DataTable)
        row = table.cursor_row
        table.clear()
        for connector in connectors:
            table.add_row(
                connector.name[:26],
                _status_cell(connector),
                connector.kind,
                (connector.purpose or connector.target)[:46],
                str(len(connector.required_by) or "-"),
            )
        if row is not None and 0 <= row < len(connectors):
            table.move_cursor(row=row)
        self._render_detail()

    # ---------------------------------------------------------------- detail

    def _current(self) -> Connector | None:
        if self._view != "servers":
            return None
        table = self.query_one("#set-table", DataTable)
        row = table.cursor_row
        if not self._connectors or row is None or row < 0 or row >= len(self._connectors):
            return None
        return self._connectors[row]

    def _current_plane(self) -> DataPlane | None:
        if self._view != "clusters":
            return None
        table = self.query_one("#set-table", DataTable)
        row = table.cursor_row
        if not self._planes or row is None or row < 0 or row >= len(self._planes):
            return None
        return self._planes[row]

    def on_data_table_row_highlighted(self) -> None:
        self._render_detail()

    def _render_detail(self) -> None:
        if self._view == "clusters":
            self._render_plane_detail()
            return
        info = self.query_one("#set-info", Static)
        connector = self._current()
        if connector is None:
            info.update("No connector selected.")
            return

        lines = [f"[b]{_escape(connector.name)}[/b]", ""]
        if connector.purpose:
            lines += [_escape(connector.purpose), ""]
        lines += [
            f"status     {_status_cell(connector)}",
            f"kind       {connector.kind}",
            f"detail     {_escape(connector.detail or '-')}",
            "",
            "[dim]starts with[/dim]",
            f"  {_escape(connector.target[:120])}",
        ]

        if connector.required_by:
            named = ", ".join(sorted(connector.required_by)[:8])
            more = len(connector.required_by) - 8
            if more > 0:
                named += f", +{more} more"
            lines += [
                "",
                f"[b]Named by {len(connector.required_by)} skill(s)[/b]",
                f"[dim]{_escape(named)}[/dim]",
                "[dim]Read from skill prose, so this is indicative, not a contract.[/dim]",
            ]
        else:
            lines += ["", "[dim]No installed skill names this connector.[/dim]"]

        if not mcp_enabled():
            lines += [
                "",
                "[yellow]Not passed to skill runs. Press c for how to enable.[/yellow]",
            ]

        info.update("\n".join(lines))

    # ----------------------------------------------------------------- hints

    def _render_plane_detail(self) -> None:
        info = self.query_one("#set-info", Static)
        plane = self._current_plane()
        if plane is None:
            info.update("No cluster selected.")
            return

        lines = [f"[b]{_escape(plane.host)}[/b]", ""]
        if plane.purpose:
            lines += [_escape(plane.purpose), ""]
        lines += [
            f"database   {_escape(plane.database or 'not recorded')}",
            f"status     {_plane_status(plane)}",
            f"used by    {plane.used_by}",
        ]
        if plane.detail:
            lines.append(f"detail     {_escape(plane.detail)}")

        if plane.used_by in ("sentry", "both"):
            lines += [
                "",
                "[b]Sentry queries this directly[/b]",
                "[dim]Not through MCP. If this is denied the console itself[/dim]",
                "[dim]cannot show its queue or SLIs.[/dim]",
            ]

        if plane.required_by:
            named = ", ".join(sorted(plane.required_by)[:8])
            more = len(plane.required_by) - 8
            if more > 0:
                named += f", +{more} more"
            lines += [
                "",
                f"[b]Named by {len(plane.required_by)} skill(s)[/b]",
                f"[dim]{_escape(named)}[/dim]",
            ]

        if plane.redacted:
            lines += [
                "",
                "[yellow]The reference redacts this hostname, so it cannot be[/yellow]",
                "[yellow]probed. It is listed because a skill depends on it.[/yellow]",
            ]
        elif plane.status == "declared":
            lines += ["", "[dim]Not probed. Press p to test access.[/dim]"]

        info.update("\n".join(lines))

    def action_copy_hint(self) -> None:
        """Show how to change a setting rather than changing it.

        A console that runs production actions should not also rewrite its own
        permissions from a keypress; and on Windows a User-scope variable does
        not reach processes whose parent predates it, so the restart note is
        part of the instruction rather than a footnote.
        """
        path = config_path(self._config) or "<path to .mcp.json>"
        self._log("")
        self._log("[b]Wire connectors into skill runs[/b]")
        self._log("  [dim]lets skills query production telemetry during a run[/dim]")
        self._log("  $env:OCE_SENTRY_ENABLE_MCP = '1'")
        self._log(f"  [dim]uses {path}[/dim]")
        self._log("  [yellow]costs more: every server's tool definitions enter the[/yellow]")
        self._log("  [yellow]prompt, so a measured run went 55s/~0 credits to[/yellow]")
        self._log("  [yellow]87s/107 credits on 536k tokens.[/yellow]")
        self._log("")
        self._log("[b]Point at a different MCP config[/b]")
        self._log("  $env:OCE_SENTRY_MCP_CONFIG = 'C:\\path\\to\\.mcp.json'")
        self._log("")
        self._log("[b]Allow skills that ask for shell[/b] [red](rarely wanted)[/red]")
        self._log("  $env:OCE_SENTRY_ALLOW_SKILL_SHELL = '1'")
        self._log("")
        self._log("[dim]Set these before launching oce-sentry. A User-scope[/dim]")
        self._log("[dim]variable does not reach an already-running shell.[/dim]")

    def action_open_source(self) -> None:
        path = config_path(self._config)
        if path is not None:
            webbrowser.open(path.parent.as_uri())
        else:
            self._log("[yellow]No MCP config found to open.[/yellow]")

    # ---------------------------------------------------------------- chrome

    def _log(self, message: str) -> None:
        self.query_one("#set-log", RichLog).write(message)

    def action_close(self) -> None:
        self.dismiss()

    def _set_status(self, message: str) -> None:
        self.query_one("#set-status", Static).update(message)


def _plane_status(plane: DataPlane) -> str:
    if plane.status == "ready":
        return "[green]ready[/green]"
    if plane.status == "denied":
        return "[red]denied[/red]"
    if plane.status == "unreachable":
        return "[red]unreachable[/red]"
    if plane.status == "redacted":
        return "[dim]redacted[/dim]"
    return "[dim]declared[/dim]"


def _status_cell(connector: Connector) -> str:
    if connector.status == "ready":
        return "[green]ready[/green]"
    if connector.status == "missing":
        return "[red]missing[/red]"
    if connector.status == "unreachable":
        return "[red]unreachable[/red]"
    return "[dim]probing[/dim]"


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")
