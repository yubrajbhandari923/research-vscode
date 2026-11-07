"""Terminal UI for a ResearchOS project (`research tui`). Needs the optional `textual` package (pip install 'research-os[tui]').

A thin view over the same core entry points the VS Code extension uses (research.rpc.Server): reading is direct,
and every change (cancel run, approve/reject proposal) goes through the existing core function after a confirm.
Approval PINs are typed into a masked field and passed straight to core; they are never stored, logged or shown.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from rich.text import Text
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import DataTable, Footer, Input, Label, Static, TabbedContent, TabPane

TABS = ["Overview", "Runs", "Findings", "Plans & Approvals", "Records", "Doctor"]
KEYS = [("1-6", "switch tab"), ("r", "refresh"), ("/", "filter tables (empty clears)"), ("k", "cancel selected run (Runs)"),
        ("a / d", "approve / reject selected proposal (Plans & Approvals)"), ("ctrl+t", "dark / light theme"),
        ("?", "this help"), ("q", "quit")]
GLYPHS = {"letters": {
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"], "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "S": [" ████", "█    ", " ███ ", "    █", "████ "], "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "C": [" ████", "█    ", "█    ", "█    ", " ████"], "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "]}}
BANNER = [" ".join(GLYPHS["letters"][c][i] for c in "RESEARCHOS") for i in range(5)]  # 59 columns
GRADIENT = [(0, 215, 255), (60, 120, 255), (200, 80, 255)]  # cyan -> blue -> magenta
STATUS = {"blue": "running queued submitted created active", "green": "passed ok succeeded completed done supported approved applied healthy",
          "red": "failed error refuted rejected", "dark_orange": "pending warn proposed needs_review blocked revision_requested ready",
          "grey62": "cancelled archived superseded abandoned"}


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
            t.append(f"{k:8}", style="bold").append(d + "\n")
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
TABLES = {"runs": "Runs", "findings": "Findings", "plans": "Plans", "approvals": "Approvals", "records": "Records"}
PANELS = {"p-status": "Runs by status", "p-runs": "Last runs", "p-appr": "Approvals", "p-find": "Latest findings",
          "p-sup": "Supervised plans", "p-sys": "Project & system"}


class ResearchTUI(App):
    CSS = """
    #tabs { height: 1fr; } TabPane { height: 1fr; padding: 0; }
    #banner { height: auto; text-wrap: nowrap; text-overflow: ellipsis; padding: 0 1; background: $surface; }
    #dialog { width: 64; max-width: 95%; height: auto; border: round $accent; background: $surface; padding: 1 2; margin: 4 2; }
    .title { text-style: bold; color: $accent; }
    TabbedContent Tab.-active { color: $accent; text-style: bold; }
    Underline > .underline--bar { color: $accent; }
    .panel, DataTable, .detail { border: round $primary; border-title-color: $accent; border-title-style: bold; }
    .panel:focus-within, DataTable:focus { border: round $accent; }
    DataTable { width: 2fr; height: 1fr; } .detail { width: 3fr; height: 1fr; padding: 0 1; }
    #dash { grid-size: 2; grid-gutter: 0 1; grid-rows: 1fr; height: 1fr; }
    #dash .panel { padding: 0 1; }
    #left { width: 2fr; } #left DataTable { width: 100%; }
    ResearchTUI.plain .panel, ResearchTUI.plain DataTable, ResearchTUI.plain .detail, ResearchTUI.plain #dialog { border: ascii $primary; }
    """
    TITLE = "ResearchOS"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("q", "quit", "Quit"), Binding("r", "refresh", "Refresh"), Binding("slash", "filter", "Filter"),
                Binding("question_mark", "help", "Help"), Binding("ctrl+t", "theme", "Theme")] + [
        Binding(str(i + 1), f"tab({i})", TABS[i], show=False) for i in range(6)
    ] + [Binding("k", "kill", "Kill run"), Binding("a", "approve", "Approve"), Binding("d", "deny", "Deny")]

    def __init__(self, root: Optional[str] = None):
        super().__init__()
        from research.store import find_root
        self.root = find_root(root or os.getcwd())
        self.srv = None
        self.snap: Dict[str, Any] = {}
        self.proposals: List[Dict[str, Any]] = []
        self.doc: Dict[str, Any] = {}
        self.filt = ""
        self.branch = ""

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Static(id="banner")
        if not self.root:
            yield Static("No ResearchOS project here.\n\nRun `research init` in your project folder, then `research tui`.\n\nPress any key to quit.", id="noproj")
            return
        with TabbedContent(id="tabs"):
            with TabPane(TABS[0], id="t1"):
                with Grid(id="dash"):
                    for pid in PANELS:
                        yield VerticalScroll(Static(id=pid), classes="panel", id=pid + "-box")
            with TabPane(TABS[1], id="t2"):
                yield Horizontal(DataTable(id="runs"), VerticalScroll(Static(id="runs-d"), classes="detail", id="runs-box"))
            with TabPane(TABS[2], id="t3"):
                yield Horizontal(DataTable(id="findings"), VerticalScroll(Static(id="findings-d"), classes="detail", id="findings-box"))
            with TabPane(TABS[3], id="t4"):
                yield Horizontal(Vertical(DataTable(id="plans"), DataTable(id="approvals"), id="left"),
                                 VerticalScroll(Static(id="approvals-d"), classes="detail", id="approvals-box"))
            with TabPane(TABS[4], id="t5"):
                yield Horizontal(DataTable(id="records"), VerticalScroll(Static(id="records-d"), classes="detail", id="records-box"))
            with TabPane(TABS[5], id="t6"):
                yield VerticalScroll(Static(id="doctor"), classes="panel", id="doctor-box")
        yield Footer()

    def on_key(self, event) -> None:
        if not self.root and not self.screen.is_modal:
            self.exit()

    def on_resize(self, event) -> None:
        self.draw_banner()

    def draw_banner(self) -> None:
        import research
        w, h = self.size
        name = self.snap.get("project", {}).get("name") or (os.path.basename(self.root) if self.root else "")
        info = Text(no_wrap=True, overflow="ellipsis")
        info.append(f"v{research.__version__}", style="bold").append(f"  {'|' if plain() else '│'}  {name}")
        if self.root:
            info.append(f"  {'|' if plain() else '│'}  {self.root}")
        if self.branch:
            info.append(f"  {'|' if plain() else '│'}  {'' if plain() else '⎇ '}{self.branch}", style="" if plain() else "dim")
        if h >= 30 and w >= 80:
            head = Text("\n").join(gradient(line) for line in BANNER)
            head.append("\n").append_text(info)
        else:
            head = Text.assemble(gradient("RESEARCHOS"), "  ", info)
            head.no_wrap, head.overflow = True, "ellipsis"
        try:
            self.query_one("#banner", Static).update(head)
        except Exception:
            pass

    def on_mount(self) -> None:
        self.register_theme(DARK)
        self.register_theme(LIGHT)
        bg = (os.environ.get("COLORFGBG") or "").split(";")[-1]
        self.theme = "textual-ansi" if plain() else "researchos-light" if bg in ("7", "15") else "researchos"
        self.set_class(plain(), "plain")
        self.draw_banner()
        if not self.root:
            return
        from research.rpc import Server
        self.srv = Server(self.root)
        self.branch = git_branch(self.root)
        cols = {"runs": ("id", "status", "started", "summary"), "findings": ("id", "status", "title"),
                "plans": ("id", "status", "title"), "approvals": ("id", "status", "proposal"),
                "records": ("id", "type", "status", "title")}
        for tid, c in cols.items():
            t = self.query_one(f"#{tid}", DataTable)
            t.cursor_type, t.zebra_stripes, t.border_title = "row", True, TABLES[tid]
            t.add_columns(*c)
        for pid, title in PANELS.items():
            self.query_one(f"#{pid}-box").border_title = title
        for bid, title in (("runs", "Run detail"), ("findings", "Finding"), ("approvals", "Detail"), ("records", "Record"), ("doctor", "Project doctor")):
            self.query_one(f"#{bid}-box").border_title = title
        self.fill_doctor()
        self.refresh_data()
        self.set_interval(5, self.refresh_data)

    # ------------------------------------------------------------------ actions
    def action_tab(self, i: int) -> None:
        if self.root:
            self.query_one("#tabs", TabbedContent).active = f"t{i + 1}"

    def action_refresh(self) -> None:
        self.fill_doctor()
        self.refresh_data()

    def action_theme(self) -> None:
        if not plain():
            self.theme = "researchos-light" if self.theme == "researchos" else "researchos"

    def action_help(self) -> None:
        self.push_screen(Help())

    def action_filter(self) -> None:
        if self.root and self._active() in ("t2", "t3", "t4", "t5"):
            def got(v: Optional[str]) -> None:
                if v is not None:
                    self.filt = v.strip()
                    self.refresh_data()
            self.push_screen(Prompt("Filter rows containing"), got)

    # ------------------------------------------------------------------ data
    def fill_doctor(self) -> None:
        import research
        from research import doctor
        try:
            self.doc = doctor.run(self.root)
            r = self.doc
            lines = Text(f"research core {research.__version__}  ({os.path.dirname(research.__file__)})\nproject: {r['root']}\n")
            lines.append("healthy: ").append_text(badge("ok" if r["healthy"] else "failed")).append(f"  {r['summary']}\n\n")
            for c in r["checks"]:
                lines.append_text(badge({"warn": "warn", "error": "error", "ok": "ok"}.get(c["level"], c["level"])))
                lines.append(f" {c['area']}: {c['title']}\n")
                if c.get("detail"):
                    lines.append(f"    {c['detail']}\n", style="dim")
        except Exception as e:  # shown, never fatal
            lines = Text(f"doctor failed: {e}")
        self.query_one("#doctor", Static).update(lines)

    def _fill(self, tid: str, rows: List[tuple]) -> None:
        t = self.query_one(f"#{tid}", DataTable)
        cur = None
        if t.row_count:
            try:
                cur = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
            except Exception:
                pass
        f = self.filt.lower()
        rows = [r for r in rows if not f or f in " ".join(str(c) for c in r[1:]).lower()]
        t.clear()
        for r in rows:
            t.add_row(*[x if isinstance(x, Text) else str(x) if x is not None else "" for x in r[1:]], key=r[0])
        t.border_title = TABLES[tid] + (f"  /{self.filt}" if f else "") + f"  ({len(rows)})"
        if cur is not None:
            try:
                t.move_cursor(row=t.get_row_index(cur))
            except Exception:
                pass

    def refresh_data(self) -> None:
        if not self.srv:
            return
        try:
            self.snap = self.srv.m_tree()
            self.proposals = self.srv.m_proposals()
        except Exception as e:
            self.query_one("#p-sys", Static).update(f"Could not read project: {e}")
            return
        T = self.snap
        runs = [r for e in T["experiments"] for r in e["runs"]]
        self._fill("runs", [(r["id"], r["id"], badge(r["status"]), r["started_at"], r["label"] or (r["command"] or "")[:60]) for r in runs])
        self._fill("findings", [(f["id"], f["id"], badge(f["status"]), f["title"]) for f in T["findings"]])
        self._fill("plans", [(p["id"], p["id"], badge(p["status"]), p["title"]) for p in T["plans"]])
        self._fill("approvals", [(x["id"], x["id"], badge(x["status"]), str(x.get("text") or x.get("kind") or "")[:60]) for x in self.proposals])
        self._fill("records", [(x["id"], x["id"], t, badge(x.get("status")), x.get("title")) for t, k in
                               (("experiment", "experiments"), ("decision", "decisions"), ("question", "questions")) for x in T[k]])
        self.draw_banner()
        self.fill_dashboard(runs)

    def fill_dashboard(self, runs: list) -> None:
        import research
        from collections import Counter
        T = self.snap
        put = lambda pid, t: self.query_one(f"#{pid}", Static).update(t)
        line = lambda *parts: Text.assemble(*parts, "\n")
        bar = "#" if plain() else "█"
        by = Counter(r["status"] for r in runs)
        t = Text()
        for st, n in by.most_common():
            t.append_text(badge(st)).append(f"  {n:3} ").append(bar * min(n, 20) + "\n", style="dim")
        put("p-status", t if by else "No runs yet.")
        t = Text()
        for r in sorted(runs, key=lambda r: (r["started_at"] or "", r["id"]), reverse=True)[:5]:
            t.append(f"{r['id']} ", style="bold").append_text(badge(r["status"])).append(f"  {r['label'] or (r['command'] or '')[:30]}\n")
        put("p-runs", t if runs else "No runs yet.")
        pend = [x for x in self.proposals if x["status"] == "pending"]
        t = Text()
        t.append(f"{'! ' if plain() else '⚠ '}{len(pend)} pending" if pend else f"{'ok ' if plain() else '✓ '}nothing pending",
                 style="" if plain() else "bold dark_orange" if pend else "green")
        for x in pend[:5]:
            t.append(f"\n{x['id']}  {str(x.get('text') or x.get('kind') or '')[:40]}")
        t.append("\n\nTab 4: a approve, d reject" if pend else "")
        put("p-appr", t)
        t = Text()
        for f in T["findings"][:5]:
            t.append(f"{f['id']} ", style="bold").append_text(badge(f["status"])).append(f"  {f['title']}\n")
        put("p-find", t if T["findings"] else "No findings yet.")
        try:
            sup = self.srv.m_managed_plans()
        except Exception:
            sup = []
        t = Text()
        for s in sup:
            t.append(f"{s['key']} ", style="bold").append_text(badge(s["state"])).append(f"  {s['progress']}  health={s['health']}\n")
        put("p-sup", t if sup else "None.")
        try:
            from research import approval
            pin = "set" if approval.status()["set"] else "not set"
        except Exception:
            pin = "?"
        d = self.doc.get("summary", {})
        t = Text.assemble((T["project"]["name"] or "", "bold"), f"\n{T['project'].get('goal') or ''}\n",
                          f"questions {len(T['questions'])}  experiments {len(T['experiments'])}  plans {len(T['plans'])}\n"
                          f"decisions {len(T['decisions'])}  findings {len(T['findings'])}\ncore {research.__version__}  PIN {pin}\ndoctor: ")
        t.append_text(badge("ok" if self.doc.get("healthy") else "failed") if self.doc else Text("n/a"))
        t.append(f"  {d.get('errors', 0)} errors, {d.get('warnings', 0)} warnings" if self.doc else "")
        put("p-sys", t)

    # ------------------------------------------------------------------ detail panes
    def on_data_table_row_highlighted(self, e: DataTable.RowHighlighted) -> None:
        self.show_detail(e.data_table.id, e.row_key.value)

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        self.show_detail(e.data_table.id, e.row_key.value)

    def show_detail(self, table: Optional[str], key: Optional[str]) -> None:
        if not key or table not in TABLES:
            return
        pane = "approvals-d" if table in ("plans", "approvals") else f"{table}-d"
        try:
            if table == "approvals":
                d = next(x for x in self.proposals if x["id"] == key)
            else:
                d = self.srv.m_show(key)
            text = render(d, skip=("events", "links", "type", "stdout_path_abs", "stderr_path_abs", "run_dir_abs"))
            if table == "runs":
                text.append("\n--- stdout ---\n" + tail(d.get("stdout_path_abs")) + "\n--- stderr ---\n" + tail(d.get("stderr_path_abs"), 15))
        except Exception as e:
            text = Text(f"Could not load {key}: {e}")
        self.query_one(f"#{pane}", Static).update(text)

    # ------------------------------------------------------------------ actions that change things (via core, after confirm)
    def _active(self) -> str:
        return self.query_one("#tabs", TabbedContent).active if self.root else ""

    def _current(self, table: str) -> Optional[str]:
        t = self.query_one(f"#{table}", DataTable)
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value

    def _do(self, fn, *a, **kw) -> None:
        from research.util import ResearchError
        try:
            fn(*a, **kw)
            self.notify("Done")
        except ResearchError as e:
            self.notify(f"{e} {e.hint or ''}".strip(), severity="error")
        except Exception as e:
            self.notify(f"Failed: {type(e).__name__}", severity="error")  # never include args: they may hold a PIN
        self.refresh_data()

    def action_kill(self) -> None:
        rid = self._current("runs") if self._active() == "t2" else None
        if not rid:
            return
        run = next((r for e in self.snap["experiments"] for r in e["runs"] if r["id"] == rid), {})
        if run.get("status") not in ("running", "queued", "submitted", "pending", "created"):
            return self.notify(f"{rid} is {run.get('status')}; nothing to cancel")
        self.push_screen(Confirm(f"Cancel run {rid}?"), lambda ok: ok and self._do(self.srv.m_run_cancel, rid))

    def _pending(self) -> Optional[str]:
        pid = self._current("approvals") if self._active() == "t4" else None
        if pid and next((x for x in self.proposals if x["id"] == pid), {}).get("status") == "pending":
            return pid
        if pid:
            self.notify(f"{pid} is not pending")
        return None

    def action_approve(self) -> None:
        from research import approval
        pid = self._pending()
        if not pid:
            return

        def decide(note: str, pin: Optional[str]) -> None:
            self._do(self.srv.m_proposal_decide, pid, True, note=note, pin=pin)

        def got_note(note: Optional[str]) -> None:
            if note is None:
                return
            if approval.status()["set"]:
                self.push_screen(Prompt("Approval PIN", password=True), lambda pin: pin is not None and decide(note, pin))
            else:
                decide(note, None)

        # Core requires a rationale for approvals by default, so ask for it after the confirm.
        self.push_screen(Confirm(f"Approve {pid}? This applies the change."),
                         lambda ok: ok and self.push_screen(Prompt("Reason for approving"), got_note))

    def action_deny(self) -> None:
        pid = self._pending()
        if pid:
            self.push_screen(Confirm(f"Reject {pid}?"), lambda ok: ok and self._do(self.srv.m_proposal_decide, pid, False))


def run(root: Optional[str] = None) -> None:
    ResearchTUI(root).run()
