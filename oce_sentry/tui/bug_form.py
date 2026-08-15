"""The CREATE BUG flow.

Three steps, each of which the operator can leave: describe it, read the draft,
file it. Nothing reaches Azure DevOps until the last one, and the draft screen
shows the exact title and body that will be created.

The middle step is the important one. A bug filed straight from a one-line note
is usually unactionable; a bug drafted by a model and filed unread is
unactionable and confidently worded, which is worse.
"""

from __future__ import annotations

import webbrowser

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Label, RadioButton, RadioSet, Static, TextArea

from ..ado import AdoClient, AdoError
from ..bugs import CATEGORIES, BugDraft, BugDraftError, draft_bug, file_bug
from ..models import Incident


class CreateBugScreen(ModalScreen[dict | None]):
    """Describe -> draft -> confirm -> file."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Draft"),
    ]

    def __init__(self, config, tokens, incident: Incident | None) -> None:
        super().__init__()
        self._config = config
        self._tokens = tokens
        self._incident = incident
        self._draft: BugDraft | None = None
        self._stage = "describe"

    def compose(self) -> ComposeResult:
        with Vertical(id="bug-form"):
            with VerticalScroll(id="bug-scroll"):
                yield Label("[b]Create a bug[/b]", id="bug-form-title")
                yield Static(self._context_line(), id="bug-form-context")
                yield Label("What is wrong?")
                yield RadioSet(
                    *[RadioButton(label, id=f"cat-{key}") for key, label in CATEGORIES],
                    id="bug-category",
                )
                yield Label("Describe it in your own words:")
                yield TextArea(id="bug-note")
            yield Static("", id="bug-form-status")
            with Horizontal(id="bug-form-buttons"):
                yield Button("Draft the bug", variant="primary", id="draft")
                yield Button("Cancel", id="cancel")
        # The modal carries its own Footer so the visible key hints belong to
        # THIS screen. Without it the footer underneath shows through, listing
        # keys that do nothing here and omitting the two that matter.
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#bug-category", RadioSet).children[0].value = True
        note = self.query_one("#bug-note", TextArea)
        note.focus()
        # Focusing the note box scrolls it into view and takes the heading with
        # it; put the form back at the top so the operator sees what they are
        # filling in.
        self.call_after_refresh(lambda: self.query_one("#bug-scroll", VerticalScroll).scroll_home(animate=False))

    def _context_line(self) -> str:
        if self._incident is None:
            return "[dim]No incident selected; the bug will be filed without incident context.[/dim]"
        return (
            f"[dim]Incident {self._incident.incident_id} - "
            f"monitor {self._incident.monitor_id or 'none recorded'} - "
            f"Sev {self._incident.severity_label}[/dim]"
        )

    def _category(self) -> str:
        radio = self.query_one("#bug-category", RadioSet)
        pressed = radio.pressed_button
        if pressed is None or pressed.id is None:
            return "other"
        return pressed.id.replace("cat-", "")

    # ------------------------------------------------------------------ draft

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "draft":
            self.action_submit()
        elif event.button.id == "file":
            self._do_file()
        elif event.button.id == "back":
            self._stage = "describe"
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        if self._stage != "describe":
            return
        note = self.query_one("#bug-note", TextArea).text.strip()
        if not note:
            self._status("[red]Describe the problem first.[/red]")
            return
        self._status("drafting ... this calls a model and takes a few seconds")
        self.query_one("#draft", Button).disabled = True
        self._draft_worker(note, self._category())

    @work(thread=True, group="bug-draft")
    def _draft_worker(self, note: str, category: str) -> None:
        try:
            draft = draft_bug(note, category, self._config, self._incident)
        except BugDraftError as exc:
            self.app.call_from_thread(self._draft_failed, str(exc))
            return
        self.app.call_from_thread(self._show_draft, draft)

    def _draft_failed(self, message: str) -> None:
        self._status(f"[red]{message}[/red]")
        self.query_one("#draft", Button).disabled = False

    def _show_draft(self, draft: BugDraft) -> None:
        self._draft = draft
        self._stage = "review"

        board_note = ""
        try:
            from ..ado import load_board

            board = load_board()
            board_note = (
                f"{board['organization']}/{board['project']} - "
                f"{board['areaPath']} - assigned to {board['assignedTo']}"
            )
        except AdoError:
            board_note = "(board configuration unreadable)"

        cost = f" - {draft.credits:g} credits" if draft.credits is not None else ""
        body_preview = _strip_html(draft.body_html)

        # Only the scrolling region is replaced. The button row is docked and
        # must survive the stage change, or the review step loses its own
        # submit -- the exact failure this layout was changed to prevent.
        scroll = self.query_one("#bug-scroll", VerticalScroll)
        scroll.remove_children()
        scroll.mount(Label("[b]Review the bug before it is filed[/b]"))
        scroll.mount(
            Static(
                f"[b]Title[/b]\n{_escape(draft.title)}\n\n"
                f"[b]Body[/b]\n{_escape(body_preview)}\n\n"
                f"[dim]Will be created in {board_note}{cost}[/dim]\n"
                f"[dim]Nothing has been created yet.[/dim]",
                id="bug-preview",
            )
        )

        buttons = self.query_one("#bug-form-buttons", Horizontal)
        buttons.remove_children()
        buttons.mount(Button("Create the bug", variant="success", id="file"))
        buttons.mount(Button("Back", id="cancel"))
        buttons.mount(Static("  esc cancel", id="bug-form-hint"))
        self._status("")

    # ------------------------------------------------------------------- file

    def _do_file(self) -> None:
        if self._draft is None:
            return
        self._status("creating the work item ...")
        self.query_one("#file", Button).disabled = True
        self._file_worker(self._draft)

    @work(thread=True, group="bug-file")
    def _file_worker(self, draft: BugDraft) -> None:
        client = AdoClient(self._tokens, timeout=self._config.query_timeout)
        try:
            created = file_bug(draft, client)
        except AdoError as exc:
            self.app.call_from_thread(self._file_failed, str(exc))
            return
        self.app.call_from_thread(self.dismiss, created)

    def _file_failed(self, message: str) -> None:
        self._status(f"[red]{message}[/red]")
        button = self.query_one("#file", Button)
        button.disabled = False

    # ----------------------------------------------------------------- chrome

    def _status(self, message: str) -> None:
        try:
            self.query_one("#bug-form-status", Static).update(message)
        except Exception:  # noqa: BLE001 - the pane is rebuilt between stages
            pass


def _strip_html(markup: str) -> str:
    """Render the HTML body as plain text for review.

    The operator is approving content, not markup; showing raw tags makes the
    draft harder to check, which defeats the point of the review step.
    """
    import re

    text = re.sub(r"<li>", "  - ", markup)
    text = re.sub(r"</p>|</li>|<br\s*/?>|<hr\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    import html as html_module

    return html_module.unescape(text).strip()


def _escape(text: str) -> str:
    return str(text).replace("[", r"\[")



