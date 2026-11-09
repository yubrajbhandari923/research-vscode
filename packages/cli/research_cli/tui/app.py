from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Static, TabbedContent, TabPane

from . import helptext, inbox as IN, views as V
from .widgets import (BANNER, DARK, LIGHT, TABS, ActionMenu, Confirm, Help, Prompt, TextBox, badge, git_branch, gradient, plain, render, tail)

TABLES = {"inbox": "Needs attention", "runs": "Runs", "findings": "Findings", "plans": "Plans", "approvals": "Approvals", "decisions": "Decisions", "records": "Records"}
PANELS = {"p-status": "Runs by status", "p-runs": "Last runs", "p-appr": "Approvals", "p-find": "Latest findings",
          "p-sup": "Supervised plans", "p-sys": "Project & system"}
COLUMNS = {"inbox": ("level", "source", "item", "blocks"), "runs": ("id", "status", "started", "summary"), "findings": ("id", "status", "title"), "plans": ("id", "status", "title"),
           "approvals": ("id", "status", "proposal", "from"), "decisions": ("id", "status", "about", "question"),
           "records": ("id", "type", "status", "title")}
EMPTY = {"inbox": "Nothing needs you right now.\n\nThis list gathers everything that is waiting for a person: decisions, approvals, experiments that need a synthesis, "
                  "evidence that cannot be verified. Things that are merely recommended are marked so, and a planned experiment is informational.\n\nPress ? for the guide.",
         "runs": "No runs yet. Track one with `research run exec EXP-001 -- <command>`.", "findings": "No findings yet.",
         "approvals": "Nothing waiting for you. Proposals from agents (and requests from chat) show up here.",
         "decisions": "No decisions waiting for you.\n\nWhen the supervisor or an agent needs a choice it asks here, with numbered options, "
                      "and in chat when that is set up (`research chat setup`).",
         "records": "No records yet."}
NO_DECISIONS_OP = "This research core has no decision requests. Update it with `research self-update`."
TAB_IDS = [f"t{i + 1}" for i in range(len(TABS))]
DECISIONS_TAB, INBOX_TAB, FILTERABLE = "t5", "t8", ("t2", "t3", "t4", "t5", "t8")
ACTION_TAB = {"kill": "t2", "approve": "t4", "deny": "t4", "answer_text": DECISIONS_TAB, "actions": INBOX_TAB}
TAB_TABLE = {"t2": "runs", "t3": "findings", "t4": "approvals", "t5": "decisions", "t6": "records", "t8": "inbox"}
PENDING_RUN = ("running", "queued", "submitted", "pending", "created")


def digit_binding(i: int) -> Binding:
    return Binding(str(i), f"digit({i})", "Pick", key_display="1-9", show=(i == 1))


class ResearchTUI(App):
    CSS = """
    #tabs { height: 1fr; } TabPane { height: 1fr; padding: 0; }
    #banner { height: auto; text-wrap: nowrap; text-overflow: ellipsis; padding: 0 1; background: $surface; }
    #status { height: auto; padding: 0 1; background: $panel; }
    #helpdlg { width: 100; max-width: 98%; height: 90%; border: round $accent; background: $surface; padding: 1 2; margin: 1 1; }
    #helpdlg #box { height: 1fr; }
    #dialog { width: 64; max-width: 95%; height: auto; max-height: 95%; border: round $accent; background: $surface; padding: 1 2; margin: 1 2; }
    .title { text-style: bold; color: $accent; }
    TabbedContent Tab.-active { color: #ffffff; text-style: bold; }   /* white on the primary block: readable in both themes */
    Underline > .underline--bar { color: $accent; }
    .panel, DataTable, .detail { border: round $primary; border-title-color: $accent; border-title-style: bold; }
    .panel:focus-within, DataTable:focus { border: round $accent; }
    DataTable { width: 2fr; height: 1fr; } .detail { width: 3fr; height: 1fr; padding: 0 1; }
    #dash { grid-size: 2; grid-gutter: 0 1; grid-rows: 1fr; height: 1fr; }
    #dash .panel { padding: 0 1; }
    #left { width: 2fr; } #left DataTable { width: 100%; }
    ResearchTUI.narrow .split { layout: vertical; }
    ResearchTUI.narrow .split > DataTable, ResearchTUI.narrow #left { width: 100%; height: 1fr; }
    ResearchTUI.narrow .detail { width: 100%; height: 1fr; }
    ResearchTUI.narrow #left { height: 2fr; } ResearchTUI.narrow #plans { height: 1fr; min-height: 4; } ResearchTUI.narrow #approvals { height: 2fr; }
    ResearchTUI.narrow #decisions { height: 1fr; } ResearchTUI.narrow #decisions-box { height: 2fr; }
    ResearchTUI.narrow #dash { grid-size: 1; grid-rows: auto; overflow-y: auto; }
    ResearchTUI.narrow #dash .panel { height: auto; max-height: 8; }
    ResearchTUI.plain .panel, ResearchTUI.plain DataTable, ResearchTUI.plain .detail, ResearchTUI.plain #dialog { border: ascii $primary; }
    """
    TITLE = "ResearchOS"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("i", "inbox", "Inbox"), Binding("x", "actions", "Actions"), Binding("w", "what", "What is this?"), Binding("k", "kill", "Cancel"), Binding("a", "approve", "Approve"), Binding("d", "deny", "Reject"),
                Binding("t", "answer_text", "Answer")] + [digit_binding(i) for i in range(1, 10)] + [
        Binding("left_square_bracket", "step(-1)", "Tabs", key_display="[ ]"), Binding("right_square_bracket", "step(1)", "Next tab", show=False),
        Binding("question_mark", "help", "Help"), Binding("q", "quit", "Quit"), Binding("slash", "filter", "Filter"),
        Binding("r", "refresh", "Refresh"), Binding("ctrl+t", "theme", "Theme")]

    def __init__(self, root: Optional[str] = None):
        super().__init__()
        from research.store import find_root
        self.root = find_root(root or os.getcwd())
        self.srv = None
        self.snap: Dict[str, Any] = {}
        self.proposals: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        self.decisions_note = ""
        self.open_count: Optional[int] = 0
        self.status: Dict[str, Any] = {}
        self.sup: List[Dict[str, Any]] = []
        self.doc: Dict[str, Any] = {}
        self.filt = ""
        self.branch = ""
        self.inbox: List[Dict[str, Any]] = []
        self.prepared: Dict[str, str] = {}       # experiment -> analyst execution that was PREPARED (not necessarily running)
        self.last: Any = None

    # ------------------------------------------------------------------ layout
    def compose(self) -> ComposeResult:
        yield Static(id="banner")
        if not self.root:
            yield Static("No ResearchOS project here.\n\nRun `research init` in your project folder, then `research tui`.\n\nPress any key to quit.", id="noproj")
            return
        yield Static(id="status")
        with TabbedContent(id="tabs"):
            with TabPane(TABS[0], id="t1"):
                with Grid(id="dash"):
                    for pid in PANELS:
                        yield VerticalScroll(Static(id=pid), classes="panel", id=pid + "-box")
            with TabPane(TABS[1], id="t2"):
                yield self.split("runs")
            with TabPane(TABS[2], id="t3"):
                yield self.split("findings")
            with TabPane(TABS[3], id="t4"):
                yield Horizontal(Vertical(DataTable(id="plans"), DataTable(id="approvals"), id="left"),
                                 VerticalScroll(Static(id="approvals-d"), classes="detail", id="approvals-box"), classes="split")
            with TabPane(TABS[4], id="t5"):
                yield self.split("decisions")
            with TabPane(TABS[5], id="t6"):
                yield self.split("records")
            with TabPane(TABS[6], id="t7"):
                yield VerticalScroll(Static(id="doctor"), classes="panel", id="doctor-box")
            with TabPane(TABS[7], id="t8"):
                yield self.split("inbox")
        yield Footer()

    @staticmethod
    def split(tid: str) -> Horizontal:
        return Horizontal(DataTable(id=tid), VerticalScroll(Static(id=f"{tid}-d"), classes="detail", id=f"{tid}-box"), classes="split")

    def on_key(self, event) -> None:
        if not self.root and not self.screen.is_modal:
            self.exit()

    def on_resize(self, event) -> None:
        self.set_class(self.size.width < 120, "narrow")
        self.draw_banner()

    def draw_banner(self) -> None:
        import research
        w, h = self.size
        name = self.snap.get("project", {}).get("name") or (os.path.basename(self.root) if self.root else "")
        sep = "|" if plain() else "│"
        info = Text(no_wrap=True, overflow="ellipsis")
        info.append(f"v{research.__version__}", style="bold").append(f"  {sep}  {name}")
        if self.root:
            info.append(f"  {sep}  {self.root}")
        if self.branch:
            info.append(f"  {sep}  {'' if plain() else '⎇ '}{self.branch}", style="" if plain() else "dim")
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
        self.theme = "ansi-dark" if plain() else "researchos-light" if bg in ("7", "15") else "researchos"
        self.set_class(plain(), "plain")
        self.set_class(self.size.width < 120, "narrow")
        self.draw_banner()
        if not self.root:
            return
        from research import api
        self.srv = api.Session(self.root)
        self.branch = git_branch(self.root)
        for tid, c in COLUMNS.items():
            t = self.query_one(f"#{tid}", DataTable)
            t.cursor_type, t.zebra_stripes, t.border_title = "row", True, TABLES[tid]
            t.add_columns(*c)
        for pid, title in PANELS.items():
            self.query_one(f"#{pid}-box").border_title = title
        for bid, title in (("runs", "Run detail"), ("findings", "Finding"), ("approvals", "Detail"), ("decisions", "Decision"), ("records", "Record"),
                           ("doctor", "Project doctor"), ("inbox", "Why it matters and what you can do")):
            self.query_one(f"#{bid}-box").border_title = title
        self.fill_doctor()
        self.refresh_data()
        self.set_interval(5, self.refresh_data)

    # ------------------------------------------------------------------ navigation
    def _idle(self) -> bool:
        """True when a key should act on the tabs: there is a project and no dialog is open."""
        return bool(self.root) and not self.screen.is_modal

    def _active(self) -> str:
        return self.screen_stack[0].query_one("#tabs", TabbedContent).active if self.root else ""

    def check_action(self, action: str, parameters: tuple) -> Optional[bool]:
        """Footer shows only the keys that work on the tab you are on."""
        if action in ACTION_TAB:
            return self._idle() and self._active() == ACTION_TAB[action]
        if action == "filter":
            return self._idle() and self._active() in FILTERABLE
        return True

    def on_tabbed_content_tab_activated(self, event) -> None:
        self.refresh_bindings()     # the footer lists the keys of the tab you are on

    def action_tab(self, i: int) -> None:
        if self._idle() and 0 <= i < len(TAB_IDS):
            self.query_one("#tabs", TabbedContent).active = TAB_IDS[i]

    def action_step(self, d: int) -> None:
        if self._idle():
            self.action_tab((TAB_IDS.index(self._active()) + d) % len(TAB_IDS))

    def action_digit(self, n: int) -> None:
        if not self._idle():
            return
        if self._active() == DECISIONS_TAB:
            self.pick_option(n)
        else:
            self.action_tab(n - 1)

    def action_refresh(self) -> None:
        if self.root:
            self.fill_doctor()
            self.refresh_data()

    def action_theme(self) -> None:
        if not plain():
            self.theme = "researchos-light" if self.theme == "researchos" else "researchos"

    def action_help(self) -> None:
        if not self.screen.is_modal:
            self.push_screen(Help())

    def _selected_item(self) -> Optional[Dict[str, Any]]:
        key = self._current("inbox") if self.root else None
        return next((x for x in self.inbox if x["key"] == key), None)

    def action_inbox(self) -> None:
        if self._idle():
            self.query_one("#tabs", TabbedContent).active = INBOX_TAB

    def action_what(self) -> None:
        """What does the selected thing mean? Opens the guide at the section that explains it."""
        if self.screen.is_modal or not self.root:
            return
        topic = "start"
        if self._active() == INBOX_TAB and (it := self._selected_item()):
            topic = it["kind"]
        elif (tid := TAB_TABLE.get(self._active())):
            row = next((r for r in [self._row_status(tid)] if r), "")
            topic = row or topic
        self.push_screen(Help(helptext.section_for(topic)))

    def _row_status(self, tid: str) -> str:
        t = self.query_one(f"#{tid}", DataTable)
        if not t.row_count:
            return ""
        for cell in t.get_row_at(t.cursor_coordinate.row):
            s = str(cell.plain if hasattr(cell, "plain") else cell).replace("● ", "").strip()
            if s in helptext.TOPIC:
                return s
        return ""

    def action_actions(self) -> None:
        """The menu of what can be done with the selected inbox item."""
        it = self._selected_item() if self._idle() and self._active() == INBOX_TAB else None
        if not it:
            return self.notify("Nothing is selected in the Inbox.")
        if not it["actions"]:
            return self.notify("There is nothing to do with this item. Press w to see what it means.")
        opts = [(a, IN.ACTION_LABELS[a]) for a in it["actions"]]
        self.push_screen(ActionMenu(it["title"][:60], opts), lambda a: a and self.run_item_action(it, a))

    def inbox_detail(self, it: Dict[str, Any]) -> Text:
        ctx = None
        ref = it["ref"]
        if ref.startswith("EXP-") and it["kind"] in ("synthesis", "failed_runs", "artifacts", "experiment"):
            ctx = IN.experiment_context(self.srv.project(sync=False), self.call, ref)
            if ref in self.prepared:
                try:
                    st = self.call("execution_show", id=self.prepared[ref])["state"]
                except Exception:       # noqa: BLE001
                    st = "unknown"
                ctx.append(f"\nAnalyst session {self.prepared[ref]}: {st}" + ("  (prepared only: it is NOT running; choose Launch analyst)\n" if st == "prepared" else "\n"),
                           style="bold")
        return IN.detail(it, ctx)

    def goto(self, tab: str, ref: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab
        tid = TAB_TABLE.get(tab)
        if not tid:
            return
        t = self.query_one(f"#{tid}", DataTable)
        try:
            t.move_cursor(row=t.get_row_index(ref))
        except Exception:       # noqa: BLE001 - the record may be filtered out; the tab is still the right place
            pass

    def run_item_action(self, it: Dict[str, Any], act: str) -> None:
        ref = it["ref"]
        if act in ("open", "open_approvals", "open_decisions"):
            return self.goto(it["tab"], ref)
        if act == "help":
            return self.push_screen(Help(helptext.section_for(it["kind"])))
        if act == "artifacts":
            rows = IN.artifact_rows(self.srv.project(sync=False), ref)
            return self.query_one("#inbox-d", Static).update(IN.detail(it, IN.artifact_text(rows)))
        if act == "recheck":
            self.refresh_data()
            return self.notify("Artifacts re-checked against the registered paths and hashes. Nothing was rewritten.")
        if act == "check":
            if self._do("experiment_check", id=ref):
                r = self.last["deterministic"]
                self.notify(f"{ref}: {len(r['validated'])} passed, {len(r['failed'])} failed, {len(r['pending'])} pending. "
                            + ("Needs you: " + "; ".join(self.last["needs_human"][:3]) if self.last["needs_human"] else "Nothing needs interpretation."), timeout=12)
            return
        if act == "hpc_sync":
            return self.push_screen(Confirm("Contact the cluster over SSH and refresh the state of this project's HPC runs?\nNothing is submitted."),
                                    lambda ok: ok and self._do("hpc_sync"))
        if act == "prepare_analyst":
            return self._prepare_analyst(ref)
        if act == "launch_analyst":
            return self._launch_analyst(ref)
        if act == "record_synthesis":
            return self._record_synthesis(ref)
        status = {"mark_completed": "completed", "mark_ready": "ready", "abandon": "abandoned"}.get(act)
        if status:
            msg = {"completed": f"Mark {ref} completed?\nYou are accepting its synthesis as the scientific conclusion.",
                   "ready": f"Mark {ref} ready?\nYou accept this proposal; it can then be run.", "abandoned": f"Abandon {ref}?\nIt is kept for the record and no longer runs."}[status]
            self.push_screen(Confirm(msg), lambda ok: ok and self._do("set_status", id=ref, status=status, reason="decided in the TUI"))

    # ---- synthesis: automatic prepare, agent-assisted launch, researcher-controlled record
    def _prepare_analyst(self, exp: str) -> None:
        if self._do("dispatch", role="analyst", target=exp, headless=True):
            d = self.last or {}
            eid = d.get("execution_id") or d.get("execution") or (d.get("run") or {}).get("execution_id")
            if eid:
                self.prepared[exp] = str(eid)
                self.notify(f"Analyst session {eid} is PREPARED, not running. Nothing started. Choose Launch analyst to start the agent (it may cost money).", timeout=12)
            else:
                self.notify("Prepared, but no execution id was returned; see `research execution list`.", severity="warning")
            self.refresh_data()

    def _launch_analyst(self, exp: str) -> None:
        eid = self.prepared.get(exp)
        if not eid:
            return self.notify("Prepare the analyst draft first (nothing has been prepared in this session).", severity="warning")
        self.push_screen(Confirm(f"Start analyst session {eid} for {exp}?\nThis launches a real agent and may incur cost. It will write an interpretation for you to review; "
                                 "it does not record anything or close the experiment."), lambda ok: ok and self._start(eid))

    def _start(self, eid: str) -> None:
        if self._do("execution_start", id=eid):
            self.notify(f"Analyst {eid} started in the background. Its result appears in the experiment's record; nothing is recorded until you do it.", timeout=10)

    def _record_synthesis(self, exp: str) -> None:
        draft = IN.synthesis_draft(self.srv.project(sync=False), self.call, exp)
        text = draft + "\n\nInterpretation (write yours, or paste the analyst's after reading it):\n"

        def got(t: Optional[str]) -> None:
            if t is None or not t.strip() or t.strip() == text.strip():
                return self.notify("Nothing recorded: write your interpretation first.", severity="warning")
            self.push_screen(Confirm(f"Record this as the synthesis of {exp}?\nIt does not close the experiment; you do that separately."),
                             lambda ok: ok and self._do("synthesize", id=exp, interpretation=t.strip()))
        self.push_screen(TextBox(f"Synthesis for {exp}: review and edit", text), got)

    def action_filter(self) -> None:
        if self._idle() and self._active() in FILTERABLE:
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
        if not rows and tid in EMPTY:
            self.query_one(f"#{tid}-d", Static).update(self.decisions_note or EMPTY[tid] if tid == "decisions" else EMPTY[tid])
        if cur is not None:
            try:
                t.move_cursor(row=t.get_row_index(cur))
            except Exception:
                pass

    def load_decisions(self) -> Optional[int]:
        """Fill self.decisions (open first). Returns the open count, None when this core has no decision ops."""
        from research import api
        self.decisions_note = ""
        if "decision_list" not in api.OPS:
            self.decisions, self.decisions_note = [], NO_DECISIONS_OP
            return None
        try:
            self.decisions = V.decision_order(self.call("decision_list", status=None))
        except Exception as e:
            self.decisions, self.decisions_note = [], f"Could not read decisions: {e}"
            return None
        return sum(1 for d in self.decisions if d["status"] in ("open", "answering"))

    def refresh_data(self) -> None:
        if not self.srv:
            return
        try:
            self.snap = self.call('tree')
            self.proposals = self.call('proposals')
        except Exception as e:
            self.query_one("#p-sys", Static).update(f"Could not read project: {e}")
            return
        T = self.snap
        runs = [r for e in T["experiments"] for r in e["runs"]]
        self.open_count = self.load_decisions()
        self.sup = V.safe(self.call, 'managed_plans', default=[]) or []
        self.status = V.gather_status(self.call, self.sup, self.usage_records, self.open_count)
        self._fill("runs", [(r["id"], r["id"], badge(r["status"]), V.when(r["started_at"]) if r["started_at"] else "", r["label"] or (r["command"] or "")[:60]) for r in runs])
        self._fill("findings", [(f["id"], f["id"], badge(f["status"]), f["title"]) for f in T["findings"]])
        self._fill("plans", [(p["id"], p["id"], badge(p["status"]), p["title"]) for p in T["plans"]])
        self._fill("approvals", V.proposal_rows(self.proposals))
        self._fill("decisions", V.decision_rows(self.decisions))
        self._fill("records", [(x["id"], x["id"], t, badge(x.get("status")), x.get("title")) for t, k in
                               (("experiment", "experiments"), ("decision", "decisions"), ("question", "questions")) for x in T[k]])
        self.inbox = IN.gather(self.call, self.srv.project(sync=False), T)
        self._fill("inbox", [IN.row(i) for i in self.inbox])
        self.draw_banner()
        bar = V.inbox_chip(IN.counts(self.inbox))
        bar.append(V.SEP, style="dim").append_text(V.status_line(self.status))
        self.query_one("#status", Static).update(bar)
        self.label_decisions_tab()
        self.fill_dashboard(runs)

    def usage_records(self) -> List[Dict[str, Any]]:
        from research import usage
        return usage.records(self.srv.project(sync=False))

    def label_decisions_tab(self) -> None:
        n = self.open_count
        c = IN.counts(self.inbox)
        try:
            self.query_one("#tabs", TabbedContent).get_tab(DECISIONS_TAB).label = TABS[4] + (f" ({n})" if n else "")
            self.query_one("#tabs", TabbedContent).get_tab(INBOX_TAB).label = TABS[7] + (f" ({c['action']}!)" if c["action"] else f" ({c['followup']})" if c["followup"] else "")
        except Exception:
            pass

    def fill_dashboard(self, runs: list) -> None:
        T = self.snap
        put = lambda pid, t: self.query_one(f"#{pid}", Static).update(t)
        put("p-status", V.panel_status(runs))
        put("p-runs", V.panel_runs(runs))
        put("p-appr", V.panel_approvals(self.proposals, self.open_count))
        put("p-find", V.panel_findings(T["findings"]))
        put("p-sup", V.panel_plans(self.sup))
        try:
            from research import approval
            pin = "set" if approval.status()["set"] else "not set"
        except Exception:
            pin = "?"
        put("p-sys", V.panel_system(T, self.doc, pin))

    # ------------------------------------------------------------------ detail panes
    def on_data_table_row_highlighted(self, e: DataTable.RowHighlighted) -> None:
        self.show_detail(e.data_table.id, e.row_key.value)

    def on_data_table_row_selected(self, e: DataTable.RowSelected) -> None:
        self.show_detail(e.data_table.id, e.row_key.value)
        if e.data_table.id == "inbox":
            self.action_actions()

    def show_detail(self, table: Optional[str], key: Optional[str]) -> None:
        if not key or table not in TABLES:
            return
        pane = "approvals-d" if table in ("plans", "approvals") else f"{table}-d"
        try:
            if table == "inbox":
                text = self.inbox_detail(next(x for x in self.inbox if x["key"] == key))
            elif table == "decisions":
                text = V.decision_detail(next(x for x in self.decisions if x["id"] == key))
            else:
                text = self.record_detail(table, key)
        except Exception as e:
            text = Text(f"Could not load {key}: {e}")
        from textual.css.query import NoMatches
        try:   # the base screen, not the active one: a dialog may be open; at start-up and shutdown there may be no screen or pane yet
            self.screen_stack[0].query_one(f"#{pane}", Static).update(text)
        except (NoMatches, IndexError):
            return

    def record_detail(self, table: str, key: str) -> Text:
        prop = table == "approvals"
        d = next(x for x in self.proposals if x["id"] == key) if prop else self.call('show', id=key)
        text = render(d, skip=("events", "links", "type", "stdout_path_abs", "stderr_path_abs", "run_dir_abs"))
        if prop:
            text = V.proposal_detail(d).append_text(text)
        if table == "records" and key.startswith("EXP-"):
            text.append("\n").append_text(IN.experiment_context(self.srv.project(sync=False), self.call, key))
        if table == "runs":
            text.append("\n--- stdout ---\n" + tail(d.get("stdout_path_abs")) + "\n--- stderr ---\n" + tail(d.get("stderr_path_abs"), 15))
        return text

    # ------------------------------------------------------------------ actions that change things (via core, after confirm)
    def _current(self, table: str) -> Optional[str]:
        t = self.query_one(f"#{table}", DataTable)
        if not t.row_count:
            return None
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value

    def call(self, name: str, **params: Any) -> Any:
        from research import api
        return api.call(name, self.srv, params, surface="tui")

    def _do(self, name: str, **params: Any) -> bool:
        """Run one op. Shows its one-line outcome; errors never echo the params (they may hold a PIN). True when it succeeded."""
        from research.util import ResearchError
        ok = False
        try:
            res = self.call(name, **params)
            self.last = res
            out = res.get("outcome") if isinstance(res, dict) else None
            odd = isinstance(res, dict) and (res.get("already") or res.get("needs_text") or res.get("needs_local"))
            self.notify(str(out or "Done"), severity="warning" if odd else "information", timeout=10 if out else 4)
            ok = True
        except ResearchError as e:
            self.notify(f"{e} {e.hint or ''}".strip(), severity="error")
        except Exception as e:
            self.notify(f"Failed: {type(e).__name__}", severity="error")
        self.refresh_data()
        return ok

    def _with_pin(self, then: Callable[[str], None], label: str = "Approval PIN") -> None:
        """Ask for the PIN (masked) and hand it to `then`. With none set, offer to set one first instead of failing."""
        from research import approval
        if approval.status()["set"]:
            self.push_screen(Prompt(label, password=True), lambda pin: pin is not None and then(pin))
        else:
            self.push_screen(Confirm("No approval PIN is set on this machine, and this action needs one.\nSet a PIN now?"),
                             lambda ok: ok and self._set_pin(then))

    def _set_pin(self, then: Callable[[str], None]) -> None:
        def again(first: Optional[str]) -> None:
            if first is None:
                return

            def check(second: Optional[str]) -> None:
                if second is None:
                    return
                if second != first:
                    return self.notify("The PINs did not match. Nothing was set.", severity="error")
                if self._do('approval_set_pin', new=first):
                    then(first)
            self.push_screen(Prompt("Repeat the PIN", password=True), check)
        self.push_screen(Prompt("New approval PIN (4+ characters)", password=True), again)

    def action_kill(self) -> None:
        rid = self._current("runs") if self._idle() and self._active() == "t2" else None
        if not rid:
            return
        run = next((r for e in self.snap["experiments"] for r in e["runs"] if r["id"] == rid), {})
        if run.get("status") not in PENDING_RUN:
            return self.notify(f"{rid} is {run.get('status')}; nothing to cancel")
        self.push_screen(Confirm(f"Cancel run {rid}?"), lambda ok: ok and self._do('run_cancel', id=rid))

    def _pending(self) -> Optional[str]:
        pid = self._current("approvals") if self._idle() and self._active() == "t4" else None
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
            self._do('proposal_decide', id=pid, approve=True, note=note, pin=pin)

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
            self.push_screen(Confirm(f"Reject {pid}?"), lambda ok: ok and self._do('proposal_decide', id=pid, approve=False))

    # ------------------------------------------------------------------ decisions
    def _open_decision(self) -> Optional[Dict[str, Any]]:
        did = self._current("decisions")
        d = next((x for x in self.decisions if x["id"] == did), None)
        if d is None:
            self.notify("No decision selected. Use [ and ] to change tab.")
        elif d["status"] != "open":
            self.notify(f"{did} is {d['status']}; nothing to answer.")
            return None
        return d

    def pick_option(self, n: int) -> None:
        d = self._open_decision()
        if not d:
            return
        if n > len(d["options"]):
            return self.notify(f"{d['id']} has {len(d['options'])} option{'s' if len(d['options']) != 1 else ''}.")
        o = d["options"][n - 1]
        why = "\nThis needs your PIN." if o.get("protected") else ""
        self.push_screen(Confirm(f"{d['id']} option {n}: {o['label']}\n{o.get('consequence') or ''}{why}\n\nRun it?"),
                         lambda ok: ok and self._run_option(d["id"], o))

    def _run_option(self, did: str, o: Dict[str, Any]) -> None:
        if not o.get("protected"):
            return self._do('decision_answer', id=did, key=o["key"], via="tui")

        def go(note: Optional[str]) -> None:
            if note is not None:
                self._with_pin(lambda pin: self._do('decision_answer', id=did, key=o["key"], via="tui", note=note, pin=pin))
        self.push_screen(Prompt("Reason for choosing this"), go)

    def action_answer_text(self) -> None:
        d = self._open_decision()
        if not d:
            return

        def got(text: Optional[str]) -> None:
            if text and text.strip():
                self.notify("Sent. The supervisor is working out what to do...", timeout=6)
                # shortcut: runs on the UI thread after the next paint; a long investigator run freezes the TUI until it returns
                self.call_after_refresh(self._do, 'decision_answer', id=d["id"], text=text.strip(), via="tui")
        self.push_screen(Prompt(f"{d['id']}: your answer in your own words"), got)


def run(root: Optional[str] = None) -> None:
    ResearchTUI(root).run()
