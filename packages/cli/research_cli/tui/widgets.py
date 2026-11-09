"""Shared TUI pieces: constants, status badges, the gradient banner, modal dialogs, themes and the record renderer."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.containers import VerticalScroll
from textual.widgets import Input, Label, OptionList, Static

TABS = ["Home", "Runs", "Findings", "Plans & approvals", "Decisions", "Experiments", "System", "Inbox", "Worktrees", "Agents", "Guide & site", "Settings"]   # in tab-id order t1..t12
from .helptext import KEYS      # noqa: E402  (one list for the Help screen and the docs)
# Scientific Midnight: teal = brand and verified evidence, violet = agents and automation, amber = needs a decision, coral = failed or blocked.
MIDNIGHT, SLATE, TEAL, VIOLET, AMBER, CORAL, CLOUD, MUTED = "#0D1626", "#172439", "#39D5C4", "#958BFF", "#F1B85B", "#F07880", "#EAF0F7", "#94A5BB"
# The mark: an R whose leg is the handle of a lens (violet), drawn in half blocks. Three rows, eight columns.
MARK = ["█▀▀▄", "█▀█▀", "█ ▀▄"]
BANNER = MARK      # kept for callers that size things by the header
MARK_STYLE = [(TEAL,) * 4, (TEAL, TEAL, TEAL, VIOLET), (TEAL, TEAL, VIOLET, VIOLET)]
STATUS = {VIOLET: "running queued submitted created active investigating verify",
          TEAL: "pass passed ok succeeded completed done supported approved applied healthy answered",
          CORAL: "fail failed error refuted rejected contradicted",
          AMBER: "pending warn proposed needs_review needs_work blocked revision_requested ready open preliminary",
          MUTED: "cancelled archived superseded abandoned expired todo reversed unknown"}   # every core status must appear (test_tui_badges)


def plain() -> bool:
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") == "dumb"


def badge(status: Any) -> Text:
    s = str(status or "")
    col = next((c for c, names in STATUS.items() if s in names.split()), None)
    return Text(s) if plain() or not col else Text(f"● {s}", style=col)


def mark_rows() -> list:
    rows = []
    for r, row in enumerate(MARK):
        t = Text(no_wrap=True)
        for ch, col in zip(row, MARK_STYLE[r]):
            t.append(ch, style="" if plain() else col)
        rows.append(t)
    return rows


def wordmark() -> Text:
    """`research` in cloud, `-os` in teal, like the logo."""
    return Text.assemble(("research", "bold"), ("-os", "bold" if plain() else f"bold {TEAL}"))


WRAP = [54]       # characters available to a detail pane; the app updates it on resize


def kv(t: Text, k: str, v: Any, key_w: int = 13) -> None:
    """A label and a value on one line; a long value wraps under itself, not under the label."""
    import textwrap
    s = "—" if v in (None, "") else str(v)
    lines = textwrap.wrap(s, width=max(18, WRAP[0] - key_w), break_long_words=True) or [""]
    t.append(k.ljust(key_w), style="" if plain() else MUTED).append(lines[0] + "\n")
    for ln in lines[1:]:
        t.append(" " * key_w + ln + "\n")


def git_branch(root: str) -> str:
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""



class Confirm(ModalScreen[bool]):
    BINDINGS = [("y", "ok", "Yes"), ("n,escape", "no", "No")]

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        yield Vertical(Label(self.text, markup=False), Label("[y] yes   [n] no"), id="dialog")

    def action_ok(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class Prompt(ModalScreen[Optional[str]]):
    """One-line input; password=True masks it (used for the PIN, which is only ever handed to core)."""
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, label: str, password: bool = False):
        super().__init__()
        self.label, self.password = label, password

    def compose(self) -> ComposeResult:
        yield Vertical(Label(self.label + "  (Enter submit, Esc cancel)", markup=False), Input(password=self.password), id="dialog")

    def on_input_submitted(self, e: Input.Submitted) -> None:
        self.dismiss(e.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


def reflow(text: str, width: int) -> Text:
    """Re-wrap the guide's prose to the dialog's width. All-caps lines are headings; table-like lines (label, spaces, text) keep one row each
    with a hanging indent; the rest of a block is joined and wrapped as a paragraph."""
    import textwrap
    out = Text()
    block: list = []

    def flush() -> None:
        if not block:
            return
        tabular = any(re.search(r"\S {3,}\S", ln) for ln in block)
        for ln in (block if tabular else [" ".join(x.strip() for x in block)]):
            indent = len(ln) - len(ln.lstrip()) if tabular else len(block[0]) - len(block[0].lstrip())
            body = ln.strip()
            m = re.match(r"\S+(?: \S+)? {3,}", body) if tabular else None
            hang = indent + (len(m.group(0)) if m else 2 if indent else 0)
            out.append(textwrap.fill(body, width=width, initial_indent=" " * indent, subsequent_indent=" " * hang) + "\n")
        block.clear()
    for ln in text.split("\n"):
        body = ln.strip()
        if not body:
            flush()
            out.append("\n")
        elif body.isupper() and len(body) < 40:
            flush()
            out.append(body + "\n", style="bold" if plain() else f"bold {TEAL}")
        else:
            block.append(ln)
    flush()
    return out


class Help(ModalScreen):
    """The built-in guide: sections you step through with the arrow keys, opened at the section that explains what you had selected."""
    BINDINGS = [("escape,question_mark,q", "app.pop_screen", "Close"), ("right,l", "step(1)", "Next"), ("left,h", "step(-1)", "Previous")]

    def __init__(self, section: str = "start"):
        super().__init__()
        from .helptext import SECTIONS
        self.names = [k for k, _ in SECTIONS]
        self.i = self.names.index(section) if section in self.names else 0

    def compose(self) -> ComposeResult:
        yield Vertical(Label("", classes="title", id="help-title"), VerticalScroll(Static(id="help-body")), Static("", id="help-nav", markup=False), id="helpdlg")

    def on_mount(self) -> None:
        self.draw()

    def action_step(self, d: int) -> None:
        self.i = (self.i + d) % len(self.names)
        self.draw()

    def draw(self) -> None:
        from .helptext import SECTIONS, body
        title = dict(SECTIONS)[self.names[self.i]]
        self.query_one("#help-title", Label).update(f"ResearchOS guide - {title}")
        self.query_one("#help-body", Static).update(reflow(body(self.names[self.i]), max(40, min(100, int(self.size.width * 0.98)) - 10)))
        self.query_one("#help-nav", Static).update("  ".join(f"[{t}]" if k == self.names[self.i] else t for k, t in SECTIONS) + "\n<- ->  sections   Esc closes")


class ActionMenu(ModalScreen[Optional[str]]):
    """What can be done with the selected item. Choosing one runs the same operation the CLI would; nothing here skips a PIN or an approval."""
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list):
        super().__init__()
        self.title_text, self.options = title, options

    def compose(self) -> ComposeResult:
        yield Vertical(Label(self.title_text, classes="title", markup=False), OptionList(*[o[1] for o in self.options], id="menu"),
                       Label("Enter choose   Esc cancel"), id="dialog")

    def on_mount(self) -> None:
        self.query_one("#menu", OptionList).focus()

    def on_option_list_option_selected(self, e: OptionList.OptionSelected) -> None:
        self.dismiss(self.options[e.option_index][0])

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextBox(ModalScreen[Optional[str]]):
    """A multi-line text the researcher edits before anything is recorded (Ctrl+S records, Esc cancels)."""
    BINDINGS = [("escape", "cancel", "Cancel"), ("ctrl+s", "save", "Record")]

    def __init__(self, label: str, text: str = ""):
        super().__init__()
        self.label, self.initial = label, text

    def compose(self) -> ComposeResult:
        from textual.widgets import TextArea
        yield Vertical(Label(self.label + "\nCtrl+S record, Esc cancel. Nothing is recorded until you save.", markup=False), TextArea(self.initial, id="box"), id="helpdlg")

    def action_save(self) -> None:
        from textual.widgets import TextArea
        self.dismiss(self.query_one("#box", TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss(None)


_QUIET = ("author_type", "author_name", "author_model", "created_at", "updated_at", "claimed_by", "claimed_at", "completed_by")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _pairs(x: Dict[str, Any]) -> str:
    return "  ".join(f"{a}={b}" for a, b in x.items() if b not in (None, "", [], {}) and not isinstance(b, (dict, list)))


def _record_line(x: Dict[str, Any]) -> Text:
    """One line for a record inside a list (a plan's tasks, a run's checks): id, status badge, title."""
    t = Text()
    ident = x.get("id") or x.get("key") or x.get("name")
    title = x.get("title") or x.get("label") or x.get("goal")
    if ident:
        t.append(f"{ident} ", style="bold")
    if x.get("status"):
        t.append_text(badge(x["status"]))
        t.append("  ")
    if title:
        t.append(str(title))
    if not (ident or title):
        t.append(_pairs(x)[:160])
    return t


def _value(k: str, v: Any) -> Text:
    from .views import when
    if k == "status":
        return badge(v)
    if isinstance(v, str) and _TS.match(v):
        return Text(when(v))
    if isinstance(v, list):
        if v and all(isinstance(x, dict) for x in v):
            t = Text()
            for x in v:
                t.append("\n  ")
                t.append_text(_record_line(x))
            return t
        return Text(", ".join(str(x) for x in v))
    if isinstance(v, dict):
        return Text(_pairs(v))
    return Text(str(v))


def render(d: Dict[str, Any], skip=("events", "links", "type")) -> Text:
    """Read-only detail pane for a record dict: headline, one quiet provenance line, then the meaningful fields (empty ones hidden,
    times formatted, nested records one per line)."""
    from .views import when
    out = Text.assemble((f"{d.get('id', '')}  {d.get('title') or d.get('label') or ''}".strip(), "bold"), "\n")
    who = d.get("author_name") or d.get("claimed_by")
    bits = [f"{who} ({d['author_type']})" if who and d.get("author_type") else (who or "")]
    bits += [f"created {when(d['created_at'])}" if d.get("created_at") else "", f"updated {when(d['updated_at'])}" if d.get("updated_at") else ""]
    quiet = " · ".join(b for b in bits if b)
    out.append(quiet + "\n\n" if quiet else "\n", style="dim")
    for k, v in d.items():
        if k in skip or k in _QUIET or k in ("id", "title") or v in (None, "", [], {}):
            continue
        out.append(f"{k}: ", style="bold")
        out.append_text(_value(k, v))
        out.append("\n")
    return out


def tail(path: Optional[str], n: int = 40) -> str:
    try:
        with open(path or "", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


DARK = Theme(name="researchos", primary=TEAL, secondary=VIOLET, accent=TEAL, warning=AMBER, error=CORAL, success=TEAL, foreground=CLOUD, background=MIDNIGHT,
             surface=SLATE, panel="#1E2E47", dark=True,
             variables={"block-cursor-foreground": MIDNIGHT, "block-cursor-background": TEAL, "footer-key-foreground": TEAL, "footer-background": MIDNIGHT,
                        "input-selection-background": "#39D5C455", "border": "#2B3E5C", "border-blurred": "#22324D", "text-muted": MUTED})
LIGHT = Theme(name="researchos-light", primary="#12857A", secondary="#5B4FD6", accent="#12857A", warning="#A86B00", error="#C23B48", success="#12857A", foreground="#0D1626",
              background="#F6F8FB", surface="#FFFFFF", panel="#E8EEF6", dark=False,
              variables={"block-cursor-foreground": "#FFFFFF", "block-cursor-background": "#12857A", "footer-key-foreground": "#12857A", "footer-background": "#E8EEF6",
                         "border": "#B9C6D8", "border-blurred": "#D3DCE8", "text-muted": "#55657C"})
