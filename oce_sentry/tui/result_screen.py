"""The result of running something.

A query kit emits a wide table -- 150 columns is normal for a monitor
breakdown -- and a terminal pane is about 116. The console still renders it,
because that output is evidence and feeds later skill runs, but reading a wide
table here means scrolling in two dimensions. `d` opens the same query in
Azure Data Explorer, where the operator is already signed in and gets sorting,
filtering and export for free.

The body is not wrapped: column alignment is the only thing that makes a table
readable, and re-flowing it destroys exactly that.

There is deliberately no `Header` on this screen. It is pushed from a worker
thread when a run finishes, and Textual's Header schedules its title
asynchronously -- if the screen mounts in that window the query for
`HeaderTitle` raises `NoMatches` and takes the app down. The heading this
screen needs is the kit name and its summary, which are in the head panel
already.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static


class ResultScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
        Binding("d", "open_explorer", "Data Explorer"),
        Binding("o", "open_file", "Open saved file"),
        Binding("f", "open_folder", "Open folder"),
    ]

    def __init__(
        self,
        title: str,
        summary: str,
        body: str,
        output_path: Path | None = None,
        ok: bool = True,
        note: str = "",
        explorer_url: str = "",
    ) -> None:
        super().__init__()
        self._title = title
        self._summary = summary
        self._body = body
        self._output_path = output_path
        self._ok = ok
        self._note = note
        self._explorer_url = explorer_url

    def compose(self) -> ComposeResult:
        yield Static("", id="result-head")
        with VerticalScroll(id="result-body"):
            # Markup off: query output is data, and a stray bracket in a
            # monitor name would otherwise be swallowed as a tag.
            yield Static("", id="result-text", markup=False)
        yield Static("", id="result-status")
        yield Footer()

    def on_mount(self) -> None:
        colour = "green" if self._ok else "red"
        head = [f"[b]{_escape(self._title)}[/b]", f"[{colour}]{_escape(self._summary)}[/{colour}]"]
        if self._note:
            head.append(f"[yellow]{_escape(self._note)}[/yellow]")
        # What happens to this output, stated rather than implied. It is
        # already saved and it already feeds the next skill run; without
        # saying so the screen reads like a dead end you have to copy out of
        # by hand.
        if self._output_path is not None and self._ok:
            head.append(
                "[dim]Saved. Skills run against this incident in the next 24h "
                "will read these rows as evidence.[/dim]"
            )
        if self._explorer_url:
            head.append(
                "[b]d[/b][dim]  open this query in Azure Data Explorer -- sortable, "
                "filterable, exportable, and you are already signed in.[/dim]"
            )
        self.query_one("#result-head", Static).update("\n".join(head))

        self.query_one("#result-text", Static).update(self._body or "(no output)")

        # The exit is named here as well as in the footer. An operator who has
        # just been dropped into a full-screen wall of table output should not
        # have to hunt for the way back.
        keys = ["esc or q  back"]
        if self._explorer_url:
            keys.append("d  Data Explorer")
        if self._output_path is not None:
            keys.append("o  open file")
            keys.append("f  open folder")
        keys.append("arrows / page up / page down  scroll")
        suffix = str(self._output_path) if self._output_path else "not saved to disk"
        self.query_one("#result-status", Static).update(
            "     ".join(keys) + f"     [dim]{suffix}[/dim]"
        )

    def on_show(self) -> None:
        """Focus the body so the arrow keys scroll it without a click."""
        self.query_one("#result-body", VerticalScroll).focus()

    def action_open_explorer(self) -> None:
        if self._explorer_url:
            webbrowser.open(self._explorer_url)

    def action_open_file(self) -> None:
        if self._output_path is not None and self._output_path.exists():
            webbrowser.open(self._output_path.as_uri())

    def action_open_folder(self) -> None:
        if self._output_path is not None:
            webbrowser.open(self._output_path.parent.as_uri())

    def action_close(self) -> None:
        self.dismiss()


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")
