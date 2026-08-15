"""The SLI view.

A separate screen rather than a tab in the incident table, because it answers a
different question. The incident queue asks "what is broken right now"; this
asks "is the service meeting its objective", which is a slower, wider question
and wants the whole terminal.

The headline is deliberately the error budget rather than the percentage. An
SLI reading 99.89% against a 99.9% objective sounds healthy and is in fact over
budget, and that misreading is the one this view exists to prevent.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..kusto import KustoClient
from ..models import SourceResult, utcnow
from ..sources.slis import Sli, fetch_slis

#: Selectable windows. An SLI is a trailing-window measure, and the window is
#: the argument -- 1h says what is happening now, 30d says whether the objective
#: is actually being met.
WINDOWS = [1, 6, 24, 72, 168, 720]


def _fmt_count(value: float) -> str:
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= limit:
            return f"{value / limit:.1f}{suffix}"
    return f"{value:.0f}"


def _window_label(hours: int) -> str:
    # 24h reads as 24h in SRE usage, not 1d; days only from 48h up.
    if hours < 48:
        return f"{hours}h"
    if hours % 24 == 0:
        return f"{hours // 24}d"
    return f"{hours}h"


class SliScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("r", "refresh", "Refresh"),
        Binding("w", "next_window", "Window"),
        Binding("e", "show_environments", "Environments"),
        Binding("g", "show_regions", "Regions"),
    ]

    def __init__(self, config, tokens) -> None:
        super().__init__()
        self._config = config
        self._client = KustoClient(tokens, timeout=config.query_timeout)
        self._slis: list[Sli] = []
        self._window_index = WINDOWS.index(24)
        self._breakdown = "environments"
        self._generation = 0
        self._last: SourceResult | None = None
        #: The window the DISPLAYED numbers were computed over. Distinct from
        #: _hours, which is the window currently selected: while a refresh is
        #: in flight the two differ, and labelling rows with the selection
        #: would claim a window the data does not come from.
        self._data_hours = WINDOWS[self._window_index]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="sli-body"):
            yield DataTable(id="sli-table", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="sli-detail"):
                yield Static("", id="sli-summary")
                yield DataTable(id="sli-breakdown", cursor_type="none", zebra_stripes=True)
        yield Static("", id="sli-status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#sli-table", DataTable)
        table.add_columns(
            "SLI", "VALUE", "OBJECTIVE", "WINDOW", "BUDGET", "REQUESTS", "FAILURES"
        )
        # Column labels are fixed at construction: the breakdown dimension is
        # named in the summary pane instead, because mutating a DataTable
        # column label after the fact breaks rendering.
        self.query_one("#sli-breakdown", DataTable).add_columns("SLICE", "VALUE", "BUDGET", "REQUESTS")
        self.refresh_slis()

    # ------------------------------------------------------------------ fetch

    @property
    def _hours(self) -> int:
        return WINDOWS[self._window_index]

    def action_refresh(self) -> None:
        self.refresh_slis()

    def action_next_window(self) -> None:
        self._window_index = (self._window_index + 1) % len(WINDOWS)
        self.refresh_slis()

    def refresh_slis(self) -> None:
        self._generation += 1
        self._set_status(f"loading {_window_label(self._hours)} ...")
        self._fetch(self._generation, self._hours)

    @work(thread=True, exclusive=True, group="slis")
    def _fetch(self, generation: int, hours: int) -> None:
        result = fetch_slis(self._config, self._client, hours=hours)
        # call_from_thread lives on the App, not on a Screen.
        self.app.call_from_thread(self._apply, generation, result, hours)

    def _apply(self, generation: int, result: SourceResult, hours: int) -> None:
        if generation != self._generation:
            return  # A newer window was requested while this was in flight.

        if not result.ok and not result.data:
            if self._last is not None:
                self._set_status(f"[red]{result.error}[/red] - showing last known data")
            else:
                self._set_status(f"[red]{result.error}[/red]")
            return

        self._last = result
        self._slis = result.data
        self._data_hours = hours
        self._render_table()

        detail = result.detail
        failed = detail.get("failed", 0)
        note = f" - [yellow]{failed} unavailable[/yellow]" if failed else ""
        self._set_status(
            f"{_window_label(self._data_hours)} window - {len(self._slis)} SLI(s){note} - "
            f"{result.age_label()} - {detail.get('database', '')}"
        )

    # ----------------------------------------------------------------- render

    def _render_table(self) -> None:
        """Not named _render: that shadows Widget._render and the screen then
        renders as nothing at all, with no error to explain why."""
        table = self.query_one("#sli-table", DataTable)
        table.clear()
        for sli in self._slis:
            if not sli.ok:
                table.add_row(
                    sli.name,
                    "[dim]unavailable[/dim]",
                    f"{sli.objective:g}%",
                    _window_label(self._data_hours),
                    "",
                    "",
                    "",
                    "",
                )
                continue

            burn = sli.error_budget_burn
            if burn is None:
                burn_cell = ""
            elif burn > 100:
                burn_cell = f"[red]{burn:.0f}%[/red]"
            elif burn > 75:
                burn_cell = f"[yellow]{burn:.0f}%[/yellow]"
            else:
                burn_cell = f"[green]{burn:.0f}%[/green]"

            value = f"{sli.value:.4f}%" if sli.value is not None else "-"
            if sli.meeting_objective is False:
                value = f"[red]{value}[/red]"

            table.add_row(
                sli.name,
                value,
                f"{sli.objective:g}%",
                # The window sits immediately before the budget because a burn
                # figure is meaningless without it: 106% over 1h and 106% over
                # 30d are very different situations.
                _window_label(self._data_hours),
                burn_cell,
                _fmt_count(sli.denominator),
                _fmt_count(sli.failures),
            )
        self._render_detail()

    def _current(self) -> Sli | None:
        table = self.query_one("#sli-table", DataTable)
        row = table.cursor_row
        if not self._slis or row is None or row < 0 or row >= len(self._slis):
            return None
        return self._slis[row]

    def on_data_table_row_highlighted(self) -> None:
        self._render_detail()

    def action_show_environments(self) -> None:
        self._breakdown = "environments"
        self._render_detail()

    def action_show_regions(self) -> None:
        self._breakdown = "regions"
        self._render_detail()

    def _render_detail(self) -> None:
        summary = self.query_one("#sli-summary", Static)
        breakdown = self.query_one("#sli-breakdown", DataTable)
        breakdown.clear()

        sli = self._current()
        if sli is None:
            summary.update("No SLI selected.")
            return

        if not sli.ok:
            summary.update(f"[b]{sli.name}[/b]\n\n[red]{sli.error}[/red]\n\n[dim]{sli.table}[/dim]")
            return

        lines = [
            f"[b]{sli.name}[/b]",
            sli.description or "",
            "",
            f"value      {sli.value:.5f}%   objective {sli.objective:g}%",
        ]
        burn = sli.error_budget_burn
        if burn is not None:
            verdict = "[red]over budget[/red]" if burn > 100 else "[green]within budget[/green]"
            lines.append(f"budget     {burn:.0f}% consumed   {verdict}")
        lines += [
            f"requests   {sli.denominator:,.0f}",
            f"failures   {sli.failures:,.0f}",
            f"windows    {sli.windows:,} evaluated",
            f"latest     {sli.latest}",
            "",
            f"[dim]{sli.table}[/dim]",
            "",
            "[dim]Geneva evaluates these windows; Sentry reads them and does not",
            "recompute reliability from raw telemetry.[/dim]",
        ]
        summary.update("\n".join(line for line in lines if line is not None))

        rows = sli.environments if self._breakdown == "environments" else sli.regions
        allowed = max(100.0 - sli.objective, 1e-9)
        for entry in rows[:40]:
            value = entry.value
            if value is None:
                continue
            entry_burn = (100.0 - value) / allowed * 100.0
            cell = f"{value:.4f}%"
            if value < sli.objective:
                cell = f"[red]{cell}[/red]"
            breakdown.add_row(entry.key, cell, f"{entry_burn:.0f}%", _fmt_count(entry.denominator))

    # ----------------------------------------------------------------- chrome

    def action_close(self) -> None:
        self.dismiss()

    def _set_status(self, message: str) -> None:
        self.query_one("#sli-status", Static).update(message)






