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
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

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
from ..packs import storage_footprint
from ..dataplanes import (
    BASELINE_ACCESS,
    DataPlane,
    discover_planes,
    plane_summary,
    probe_plane,
    reference_repo,
)


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
        self._notice = ""
        self._show_hints = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="set-body"):
            yield Static("", id="set-summary")
            yield DataTable(id="set-table", cursor_type="row", zebra_stripes=True)
            # One full-width pane rather than a split. The right half used to
            # be a log that was empty most of the time, which cost half the
            # width of the only part of this screen carrying real detail --
            # and access requirements do not fit in sixty columns.
            with VerticalScroll(id="set-detail"):
                yield Static("", id="set-info")
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
            table.add_columns("CLUSTER", "DATABASE", "STATUS", "USED BY", "ACCESS NEEDED", "SKILLS")

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
            access = plane.access.short
            table.add_row(
                plane.host[:44],
                (plane.database or "-")[:16],
                _plane_status(plane),
                plane.used_by,
                access if plane.access.documented else f"[dim]{access}[/dim]",
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
        repo = reference_repo()

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
        # Access requirements are the team's facts, not Sentry's. Say where
        # this run is reading them from.
        source = (
            f"access read from {repo.name}"
            if repo is not None
            else "[yellow]access from built-in snapshot - no RCA checkout found[/yellow]"
        )

        # An operator asking what this thing stores deserves a number.
        size, files = storage_footprint(self._config)
        storage = f"local storage {size / 1024 / 1024:.1f} MB in {files} file(s)"

        self.query_one("#set-summary", Static).update(
            f"{headline}\n"
            f"config     {path or 'none found'}\n"
            f"policy     {self._config.policy.label}     {shell}     {source}\n"
            f"storage    {self._config.state_dir}     [dim]{storage}[/dim]"
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

        lines = [f"[b]{_escape(connector.name)}[/b]"]
        if connector.purpose:
            lines.append(_escape(connector.purpose))
        lines += [
            "",
            f"status     {_status_cell(connector)}"
            + (f"   [dim]{_escape(connector.detail)}[/dim]" if connector.detail else ""),
            f"kind       {connector.kind}",
            "",
            "[dim]starts with[/dim]",
            f"  {_escape(connector.target[:150])}",
        ]

        # A server being on PATH is not access. The clusters behind it are
        # where an operator is actually refused, so the access requirement is
        # shown here too rather than only under v.
        planes = [p for p in self._planes if _serves(connector, p)]
        if planes:
            lines += ["", f"[b]ACCESS YOU NEED[/b]  [dim]({len(planes)} cluster(s))[/dim]"]
            for plane in planes[:8]:
                requirement = (
                    plane.access.requirement
                    if plane.access.documented
                    else "not documented - ask the owning team"
                )
                lines.append(f"  {_escape(plane.host.split('.')[0]):<26} {_escape(requirement)}")
            if len(planes) > 8:
                lines.append(f"  [dim]+{len(planes) - 8} more, press v[/dim]")
            lines.append(f"  [dim]{_escape(BASELINE_ACCESS)}[/dim]")

        if connector.required_by:
            named = ", ".join(sorted(connector.required_by))
            lines += [
                "",
                f"[b]NEEDED BY {len(connector.required_by)} SKILL(S)[/b]",
                f"  [dim]{_escape(named)}[/dim]",
                "  [dim]Read from skill prose, so indicative rather than a contract.[/dim]",
            ]
        else:
            lines += ["", "[dim]No installed skill names this connector.[/dim]"]

        if not mcp_enabled():
            lines += [
                "",
                "[yellow]Not passed to skill runs, so nothing here is reachable[/yellow]",
                "[yellow]by a skill yet.[/yellow]",
            ]

        lines += self._hint_lines()
        info.update("\n".join(lines))

    # ----------------------------------------------------------------- hints

    def _render_plane_detail(self) -> None:
        info = self.query_one("#set-info", Static)
        plane = self._current_plane()
        if plane is None:
            info.update("No cluster selected.")
            return

        lines = [f"[b]{_escape(plane.host)}[/b]"]
        if plane.purpose:
            lines.append(_escape(plane.purpose))
        lines += [
            "",
            f"database   {_escape(plane.database or 'not recorded')}",
            f"status     {_plane_status(plane)}"
            + (f"   [dim]{_escape(plane.detail)}[/dim]" if plane.detail else ""),
            f"used by    {plane.used_by}"
            + (
                "   [dim]this console queries it directly, not through MCP[/dim]"
                if plane.used_by in ("sentry", "both")
                else ""
            ),
        ]

        # The access block is the point of this screen: a denied cluster is
        # useless information without the name of the thing to go and request.
        lines += ["", "[b]ACCESS YOU NEED[/b]"]
        if plane.access.documented:
            provenance = (
                f"documented in {plane.access.source}"
                if plane.access.live
                else f"[yellow]from a built-in {plane.access.source}[/yellow] "
                f"[dim]- may be out of date[/dim]"
            )
            lines += [
                f"  {_escape(plane.access.requirement)}",
                f"  [dim]request:[/dim] {_escape(plane.access.request_url)}",
                f"  [dim]{_escape(provenance) if plane.access.live else provenance}[/dim]",
            ]
        else:
            lines += [
                "  [dim]Not documented in the ODSP onboarding guide.[/dim]",
                "  [dim]Ask the owning team rather than guessing an entitlement;[/dim]",
                "  [dim]the wrong request goes to the wrong approver.[/dim]",
            ]
        lines.append(f"  [dim]{_escape(BASELINE_ACCESS)}[/dim]")

        lines += ["", "[b]WITHOUT IT[/b]", f"  {_escape(plane.consequence)}"]

        if plane.required_by:
            named = ", ".join(sorted(plane.required_by))
            lines += [
                "",
                f"[b]NEEDED BY {len(plane.required_by)} SKILL(S)[/b]",
                f"  [dim]{_escape(named)}[/dim]",
            ]

        if plane.redacted:
            lines += [
                "",
                "[yellow]The reference redacts this hostname, so it cannot be probed.[/yellow]",
                "[yellow]It is listed because a skill depends on it.[/yellow]",
            ]
        elif plane.status == "declared":
            lines += ["", "[dim]Not probed. Press p to test access from here.[/dim]"]
        elif plane.status == "denied":
            lines += [
                "",
                "[red]This cluster refused your identity. Request the access above,[/red]",
                "[red]then re-run az login so the new group is in your token.[/red]",
            ]

        lines += self._hint_lines()
        info.update("\n".join(lines))

    def _hint_lines(self) -> list[str]:
        if not self._show_hints:
            return ["", "[dim]Press c for how to change these settings.[/dim]"]
        path = config_path(self._config) or "<path to .mcp.json>"
        return [
            "",
            "[b]HOW TO CHANGE THESE SETTINGS[/b]",
            "  [dim]Set before launching oce-sentry. A User-scope variable does[/dim]",
            "  [dim]not reach an already-running shell.[/dim]",
            "",
            "  Wire connectors into skill runs",
            "    $env:OCE_SENTRY_ENABLE_MCP = '1'",
            f"    [dim]uses {_escape(str(path))}[/dim]",
            "    [yellow]costs more: every server's tool definitions enter the prompt,[/yellow]",
            "    [yellow]so a measured run went 28.4 credits to 107 on 536k tokens.[/yellow]",
            "",
            "  Point at a different MCP config",
            "    $env:OCE_SENTRY_MCP_CONFIG = 'C:\\path\\to\\.mcp.json'",
            "",
            "  Allow skills that ask for shell [red](rarely wanted)[/red]",
            "    $env:OCE_SENTRY_ALLOW_SKILL_SHELL = '1'",
        ]

    def action_copy_hint(self) -> None:
        """Show how to change a setting rather than changing it.

        A console that runs production actions should not also rewrite its own
        permissions from a keypress.
        """
        self._show_hints = not self._show_hints
        self._render_detail()

    def action_open_source(self) -> None:
        path = config_path(self._config)
        if path is not None:
            webbrowser.open(path.parent.as_uri())
        else:
            self._log("[yellow]No MCP config found to open.[/yellow]")

    # ---------------------------------------------------------------- chrome

    def _log(self, message: str) -> None:
        """Transient messages go to the status line.

        There is no log pane any more: it was empty most of the time and cost
        half the width of the detail that operators actually came for.
        """
        self._notice = message
        self._set_status(message)

    def action_close(self) -> None:
        self.dismiss()

    def _set_status(self, message: str) -> None:
        self.query_one("#set-status", Static).update(message)


def _serves(connector: Connector, plane: DataPlane) -> bool:
    """Whether this MCP server is how a skill reaches this cluster.

    Only `azure` brokers Kusto -- the RCA reference is explicit that even the
    EU IcM clusters go through it per-call rather than through a separate
    server. Sentry's own planes are excluded because it queries them directly.
    """
    if connector.name != "azure":
        return False
    return plane.used_by in ("skills", "both")


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
