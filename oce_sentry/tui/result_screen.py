"""The result of running something.

A query kit emits a wide table -- 150 columns is normal for a monitor
breakdown -- and the queue's side pane is about 35. Writing one into the other
wrapped every row four times and turned a readable table into noise. Results
now get the full width of the terminal, and the table is not wrapped at all:
column alignment is the only thing that makes it readable, and re-flowing it
destroys exactly that.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static


class ResultScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "close", "Back"),
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
    ) -> None:
        super().__init__()
        self._title = title
        self._summary = summary
        self._body = body
        self._output_path = output_path
        self._ok = ok
        self._note = note

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
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
        self.query_one("#result-head", Static).update("\n".join(head))

        self.query_one("#result-text", Static).update(self._body or "(no output)")

        # The exit is named here as well as in the footer. An operator who has
        # just been dropped into a full-screen wall of table output should not
        # have to hunt for the way back.
        keys = "esc or q  back to the queue"
        if self._output_path is not None:
            keys += "     o  open the saved file     f  open the folder"
            self.query_one("#result-status", Static).update(
                f"{keys}     [dim]{self._output_path}[/dim]"
            )
        else:
            self.query_one("#result-status", Static).update(
                f"{keys}     [dim]not saved to disk[/dim]"
            )

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
