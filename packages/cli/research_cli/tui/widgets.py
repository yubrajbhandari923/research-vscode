"""Shared TUI pieces: constants, status badges, the gradient banner, modal dialogs, themes and the record renderer."""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Input, Label, Static

TABS = ["Overview", "Runs", "Findings", "Plans & Approvals", "Decisions", "Records", "Doctor"]
KEYS = [("1-7", "switch tab (not on Decisions)"), ("[ ]", "previous / next tab"), ("r", "refresh"), ("/", "filter rows (empty clears)"),
        ("k", "cancel the selected run"), ("a / d", "approve / reject a proposal (PIN)"), ("1-9", "Decisions: pick that option"),
        ("t", "Decisions: answer in your own words"), ("ctrl+t", "dark / light theme"), ("?", "this help"), ("q", "quit")]
GLYPHS = {"letters": {
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"], "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "], "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "C": [" ████", "█    ", "█    ", "█    ", " ████"], "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "]}}
BANNER = [" ".join(GLYPHS["letters"][c][i] for c in "RESEARCHOS") for i in range(5)]  # 59 columns
GRADIENT = [(0, 215, 255), (60, 120, 255), (200, 80, 255)]  # cyan -> blue -> magenta
STATUS = {"blue": "running queued submitted created active", "green": "passed ok succeeded completed done supported approved applied healthy",
          "red": "failed error refuted rejected", "dark_orange": "pending warn proposed needs_review blocked revision_requested ready open",
          "grey62": "cancelled archived superseded abandoned expired"}


def plain() -> bool:
    return bool(os.environ.get("NO_COLOR")) or os.environ.get("TERM") == "dumb"


def badge(status: Any) -> Text:
    s = str(status or "")
    col = next((c for c, names in STATUS.items() if s in names.split()), None)
    return Text(s) if plain() or not col else Text(f"● {s}", style=f"bold {col}")


def gradient(text: str) -> Text:
    out = Text(no_wrap=True)
    n = max(len(text) - 1, 1)
    for i, ch in enumerate(text):
        x = i / n * (len(GRADIENT) - 1)
        lo = min(int(x), len(GRADIENT) - 2)
        c = [round(GRADIENT[lo][j] + (GRADIENT[lo + 1][j] - GRADIENT[lo][j]) * (x - lo)) for j in range(3)]
        out.append(ch, style="" if plain() else f"bold rgb({c[0]},{c[1]},{c[2]})")
    return out


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


class Help(ModalScreen):
    BINDINGS = [("escape,question_mark,q", "app.pop_screen", "Close")]

    def compose(self) -> ComposeResult:
        t = Text()
        for k, d in KEYS:
            t.append(f"{k:7} ", style="bold").append(d + "\n")
        yield Vertical(Label("ResearchOS keys", classes="title"), Static(t), Label("Esc closes"), id="dialog")


def render(d: Dict[str, Any], skip=("events", "links", "type")) -> Text:
    """Read-only detail pane for a record dict."""
    out = Text.assemble((f"{d.get('id', '')}  {d.get('title') or d.get('label') or ''}".strip(), "bold"), "\n\n")
    for k, v in d.items():
        if k in skip or k in ("id", "title") or v in (None, "", [], {}):
            continue
        out.append(f"{k}: ", style="bold")
        out.append_text(badge(v) if k == "status" else Text(str(v)))
        out.append("\n")
    return out


def tail(path: Optional[str], n: int = 40) -> str:
    try:
        with open(path or "", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


DARK = Theme(name="researchos", primary="#3c78ff", secondary="#c850ff", accent="#00d7ff", warning="#ffaf00", error="#ff5f5f",
             success="#5fd787", foreground="#e4e4ef", background="#10121c", surface="#171a28", panel="#1e2236", dark=True)
LIGHT = Theme(name="researchos-light", primary="#2457d6", secondary="#9b30d0", accent="#0086b3", warning="#c77700", error="#c62828",
              success="#2e7d32", foreground="#1c1e2a", background="#f6f7fb", surface="#ffffff", panel="#e8ebf5", dark=False)
