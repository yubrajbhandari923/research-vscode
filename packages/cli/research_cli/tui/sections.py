"""The sections that used to live only in the VS Code Control Center: worktrees and promotion, agents, the guide and website, and every setting.
Each section is a table plus a detail pane plus a menu of actions; every action calls an existing operation (the same one the CLI and the extension call), so
approvals, PIN gates and governance are unchanged."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from rich.text import Text

from .widgets import AMBER, MUTED, TEAL, VIOLET, badge, kv, plain

Row = Tuple[Any, ...]


def short(v: Any, n: int = 48) -> str:
    s = "—" if v in (None, "") else (", ".join(map(str, v)) if isinstance(v, list) else str(v))
    return s if len(s) <= n else s[:n - 1] + "…"


def _line(t: Text, k: str, v: Any) -> None:
    kv(t, k, v)


def _head(t: Text, title: str, sub: str = "") -> None:
    t.append(title + "\n", style="bold")
    if sub:
        t.append(sub + "\n", style="" if plain() else MUTED)
    t.append("\n")


class Section:
    table = ""
    title = ""
    columns: Tuple[str, ...] = ()
    empty = ""
    hint = ""

    def rows(self, app) -> List[Row]:
        return []

    def detail(self, app, key: str) -> Text:
        return Text("")

    def actions(self, app, key: str) -> List[Tuple[str, str]]:
        return []

    def act(self, app, key: str, action: str) -> None:
        return None


# ----------------------------------------------------------------------------------------------- worktrees and promotion
class Work(Section):
    table, title = "work", "Worktrees and promotion"
    columns = ("Kind", "Item", "Experiment", "State")
    empty = ("No worktrees yet.\n\nEvery experiment can have its own git worktree so runs never touch your main checkout. Fork one experiment from another's verified code "
             "with the Fork action, or run a plan: the supervisor creates them as it goes.\n\nPromotion moves verified code into main, one human-approved step at a time.")

    def rows(self, app) -> List[Row]:
        out: List[Row] = []
        for w in app.safe("workspaces") or []:
            out.append((f"ws:{w['id']}", Text("worktree", style="" if plain() else VIOLET), w["id"], w.get("experiment") or "", badge(w.get("status")) if w.get("present", True) else Text("missing on this machine", style="" if plain() else AMBER)))
        for x in app.safe("promotions") or []:
            out.append((f"pr:{x['id']}", Text("promotion", style="" if plain() else TEAL), x["id"], x.get("experiment") or "", badge(x.get("state"))))
        return out

    def detail(self, app, key: str) -> Text:
        kind, _, ident = key.partition(":")
        t = Text()
        if kind == "ws":
            w = next((x for x in app.safe("workspaces") or [] if x["id"] == ident), {})
            _head(t, f"{ident}  worktree of {w.get('experiment') or '?'}", "An isolated git worktree: runs and agents change files here, never in main.")
            for k in ("branch", "path", "base_rev", "status", "tasks", "created_at"):
                _line(t, k.replace("_", " "), short(w.get(k), 90))
            if w.get("present") is False:
                t.append("\nThe folder is not on this machine (another computer, or it was archived). Its commits are still in git.\n", style="" if plain() else AMBER)
            t.append("\nFork starts a new experiment from this one's verified code without promoting it; the new worktree is pinned to that revision.\n", style="" if plain() else MUTED)
        else:
            p = next((x for x in app.safe("promotions") or [] if x["id"] == ident), {})
            _head(t, f"{ident}  {p.get('title') or ''}", "Verified code on its way into main. Each gate is a separate, human-approved step.")
            for k in ("state", "experiment", "branch", "base_main", "candidate_head", "reason"):
                _line(t, k.replace("_", " "), short(p.get(k), 90))
            t.append("\nGates: prepare → tests → independent code review → your approval (PIN) → integrate. Nothing merges by itself unless you authorized unattended promotion for a plan.\n",
                     style="" if plain() else MUTED)
        return t

    def actions(self, app, key: str) -> List[Tuple[str, str]]:
        kind = key.partition(":")[0]
        if kind == "ws":
            return [("fork", "Fork an experiment from this worktree's code"), ("archive", "Archive this worktree (keeps the commits)")]
        return [("diff", "Show the diff"), ("abandon", "Abandon this promotion")]

    def act(self, app, key: str, action: str) -> None:
        kind, _, ident = key.partition(":")
        from .widgets import Confirm, Prompt
        if action == "fork":
            w = next((x for x in app.safe("workspaces") or [] if x["id"] == ident), {})
            exp = w.get("experiment")
            if not exp:
                return app.notify("This worktree has no experiment to fork from.", severity="warning")
            app.push_screen(Prompt(f"Title of the new experiment forked from {exp}'s verified code"),
                            lambda t: t and t.strip() and app._do("experiment_fork", id=exp, title=t.strip()))
        elif action == "archive":
            app.push_screen(Confirm(f"Archive {ident}? The worktree folder is removed from disk; its commits stay in git."), lambda ok: ok and app._do("workspace_archive", id=ident))
        elif action == "diff":
            d = app.safe("promotion_diff", id=ident) or {}
            app.query_one("#work-d").update(Text(str(d.get("diff") or d.get("text") or d)[:6000]))
        elif action == "abandon":
            app.push_screen(Confirm(f"Abandon {ident}? The candidate is kept on disk; nothing merges."), lambda ok: ok and app._do("promotion_action", id=ident, action="abandon", confirm=True))


# ----------------------------------------------------------------------------------------------- agents
class Agents(Section):
    table, title = "agents", "Agents"
    columns = ("Agent", "State", "Model", "Profiles")
    empty = "No agent providers are configured. Run `research agent setup claude` (or codex, agy, opencode)."

    def rows(self, app) -> List[Row]:
        d = app.safe("providers") or {}
        out: List[Row] = []
        for p in d.get("providers") or []:
            mark = Text("● ready", style="" if plain() else TEAL) if p.get("ready") else Text("○ not ready" if p.get("installed") else "○ not installed", style="" if plain() else AMBER)
            model = (p.get("models") or {}).get("default") if isinstance(p.get("models"), dict) else None
            star = " (default)" if p.get("name") == d.get("default") or p.get("provider") == d.get("default") else ""
            out.append((f"prov:{p.get('name') or p['provider']}", (p.get("label") or p["provider"]) + star, mark, short(model), short([x if isinstance(x, str) else x.get("name") for x in p.get("profiles") or []], 40)))
        for e in (app.safe("executions") or [])[-12:][::-1]:
            out.append((f"exe:{e['id']}", f"run {e['id']}", badge(e.get("state")), short(e.get("role")), short(e.get("target") or e.get("task"))))
        return out

    def detail(self, app, key: str) -> Text:
        kind, _, ident = key.partition(":")
        t = Text()
        if kind == "prov":
            p = next((x for x in (app.safe("providers") or {}).get("providers", []) if (x.get("name") or x["provider"]) == ident), {})
            _head(t, p.get("label") or ident, "An agent CLI that Research OS can dispatch work to. Which one does which job is decided by roles and the strategy.")
            for k in ("command", "version", "auth", "installed", "ready", "mcp_registered", "instructions"):
                _line(t, k.replace("_", " "), short(p.get(k), 80))
            m = p.get("models")
            if isinstance(m, dict):
                for lvl, v in m.items():
                    _line(t, f"model {lvl}", short(v.get("model") if isinstance(v, dict) else v))
            t.append("\nSet up or change it with `research agent setup NAME`; assign roles in Settings (agents.*). Agents can read and write research records but never approve their own work.\n", style="" if plain() else MUTED)
        else:
            e = app.safe("execution_show", id=ident) or {}
            _head(t, f"Agent session {ident}", "One dispatch of an agent. 'prepared' means a brief exists but nothing is running.")
            for k in ("state", "role", "target", "profile", "model", "mode", "started_at", "ended_at", "exit_code"):
                _line(t, k.replace("_", " "), short(e.get(k)))
        return t

    def actions(self, app, key: str) -> List[Tuple[str, str]]:
        if key.startswith("exe:"):
            return [("start", "Start (prepared headless sessions only)"), ("stop", "Stop this session")]
        return []

    def act(self, app, key: str, action: str) -> None:
        ident = key.partition(":")[2]
        from .widgets import Confirm
        if action == "start":
            app.push_screen(Confirm(f"Start agent session {ident}?\nThis launches a real agent and may incur cost."), lambda ok: ok and app._do("execution_start", id=ident))
        elif action == "stop":
            app.push_screen(Confirm(f"Stop agent session {ident}?"), lambda ok: ok and app._do("execution_stop", id=ident))


# ----------------------------------------------------------------------------------------------- guide and website
class Guide(Section):
    table, title = "guide", "Guide and website"
    columns = ("Item", "State", "Detail")
    empty = ""

    def _site(self, app) -> Dict[str, Any]:
        return app.safe("site_status") or {}

    def rows(self, app) -> List[Row]:
        s = self._site(app)
        if not s:
            return []
        out: List[Row] = []
        q = s.get("quarto") or {}
        out.append(("site", "Website", badge("ok") if s.get("guide") else Text("not created", style="" if plain() else AMBER), short(s.get("guide_dir") or "run Create website", 50)))
        out.append(("quarto", "Quarto", badge("ok") if q.get("ok") else Text("missing", style="" if plain() else AMBER), short(q.get("path") or "; ".join(q.get("problems") or []), 50)))
        wf = s.get("workflow") or {}
        out.append(("ci", "Pages workflow", badge("ok") if wf.get("exists") else Text("missing", style="" if plain() else AMBER), short(wf.get("path"), 50)))
        rem = s.get("remote") or {}
        out.append(("remote", "GitHub remote", badge("ok") if rem.get("url") else Text("local only", style="" if plain() else MUTED), short(rem.get("site_url") or rem.get("url") or "works without one", 50)))
        for e in s.get("experiments") or []:
            out.append((f"exp:{e['experiment']}", f"Experiment {e['experiment']}", badge("ok") if e["status"] == "published" else Text("unpublished", style="" if plain() else MUTED), f"/{e['slug']}/ · {e['pages']} page(s)"))
        return out

    def detail(self, app, key: str) -> Text:
        s = self._site(app)
        t = Text()
        if key.startswith("exp:"):
            e = next((x for x in s.get("experiments") or [] if x["experiment"] == key[4:]), {})
            _head(t, f"{key[4:]} on the website", "An editable draft page and the experiment's committed report and figures.")
            for k in ("status", "slug", "commit", "pages"):
                _line(t, k, e.get(k))
            if e.get("edited_by_you"):
                t.append("\nYour edits are kept in: " + ", ".join(e["edited_by_you"]) + "\n")
            t.append("\nUnpublish moves the folder to _unpublished/. Nothing is deleted, and publishing it again restores it.\n", style="" if plain() else MUTED)
            return t
        _head(t, "Research website", "Your guide as one Quarto site, previewed locally and published to GitHub Pages when YOU push. Research OS never pushes.")
        for n in s.get("next") or []:
            t.append(f"  {n}\n")
        if not s.get("next"):
            t.append("  Built and committed. Push when you are ready.\n")
        t.append("\nPreview locally: `research site preview` (live reload, no GitHub needed).\n", style="" if plain() else MUTED)
        return t

    def actions(self, app, key: str) -> List[Tuple[str, str]]:
        s = self._site(app)
        if key.startswith("exp:"):
            e = next((x for x in s.get("experiments") or [] if x["experiment"] == key[4:]), {})
            return [("remove", "Unpublish (move out of the site, nothing deleted)")] if e.get("status") == "published" else [("add", "Publish again")]
        acts = [("add_new", "Publish a verified experiment…"), ("build", "Build the site now (checks links, never runs code)")]
        if not s.get("guide") or not (s.get("workflow") or {}).get("exists"):
            acts.insert(0, ("init", "Create or repair the website files (never overwrites)"))
        return acts

    def act(self, app, key: str, action: str) -> None:
        from .widgets import Confirm, Prompt
        if action == "init":
            app._do("site_init", apply=True)
        elif action == "build":
            app.notify("Building… (this can take a minute)", timeout=4)
            app.call_after_refresh(app._do, "guide_build", edition="scientific")
        elif action == "add_new":
            app.push_screen(Prompt("Experiment to publish (EXP-001). It must be verified and have committed reports."), lambda t: t and t.strip() and app._do("site_add", experiment=t.strip()))
        elif action == "add":
            app._do("site_add", experiment=key[4:])
        elif action == "remove":
            app.push_screen(Confirm(f"Unpublish {key[4:]}? Its folder moves to _unpublished/; nothing is deleted. The live site changes only after you commit and push."),
                            lambda ok: ok and app._do("site_remove", experiment=key[4:], apply=True))


# ----------------------------------------------------------------------------------------------- settings
class Settings(Section):
    table, title = "settings", "Settings"
    columns = ("Area", "Setting", "Value")
    empty = "No settings matched."

    def _opts(self, app) -> List[Dict[str, Any]]:
        if app._settings_cache is None:
            d = app.safe("settings") or {}
            app._settings_cache = d
        return (app._settings_cache or {}).get("options") or []

    def rows(self, app) -> List[Row]:
        cats = {c["id"]: c["label"] for c in (app._settings_cache or app.safe("settings") or {}).get("categories", [])}
        out: List[Row] = []
        last = None
        for o in self._opts(app):
            v = o.get("value")
            val = Text(short(v, 26), style="" if plain() or v == o.get("default") else TEAL)
            cat = cats.get(o["category"], o["category"])
            out.append((o["key"], Text(cat if cat != last else "", style="" if plain() else "bold"), short(o["label"], 40), val))
            last = cat
        return out

    def detail(self, app, key: str) -> Text:
        o = next((x for x in self._opts(app) if x["key"] == key), None)
        t = Text()
        if not o:
            return t
        _head(t, o["label"], key)
        t.append(o["help"] + "\n\n")
        _line(t, "value", short(o.get("value"), 80))
        _line(t, "default", short(o.get("default"), 80))
        _line(t, "set in", o.get("source"))
        if o.get("choices"):
            _line(t, "choices", ", ".join(map(str, o["choices"])))
        if o.get("min") is not None or o.get("max") is not None:
            _line(t, "range", f"{o.get('min')} to {o.get('max')}" + (f" {o['unit']}" if o.get("unit") else ""))
        if o.get("why"):
            t.append("\nWhy it exists  ", style="bold").append(o["why"] + "\n")
        if o.get("effect"):
            t.append("What changes   ", style="bold").append(o["effect"] + "\n")
        if o.get("risk") != "normal" or o.get("approval"):
            t.append("\nThis is a governance setting: changing it needs your approval PIN, and agents cannot change it.\n", style="" if plain() else AMBER)
        if o.get("restart"):
            t.append("Restart the supervisor after changing it.\n", style="" if plain() else MUTED)
        t.append("\nEnter or x to change it.\n", style="" if plain() else MUTED)
        return t

    def actions(self, app, key: str) -> List[Tuple[str, str]]:
        return [("edit", "Change this setting…"), ("reset", "Reset to the default (remove from this project)")]

    def act(self, app, key: str, action: str) -> None:
        o = next((x for x in self._opts(app) if x["key"] == key), None)
        if not o:
            return
        from .widgets import ActionMenu, Prompt
        if action == "reset":
            return self._apply(app, o, None, remove=True)
        if o["type"] == "bool":
            return self._apply(app, o, not bool(o.get("value")))
        if o.get("choices"):
            app.push_screen(ActionMenu(o["label"], [(str(c), str(c)) for c in o["choices"]]), lambda c: c is not None and self._apply(app, o, c))
            return
        app.push_screen(Prompt(f"{o['label']} (now {short(o.get('value'), 30)}; empty keeps it)"), lambda v: v and v.strip() and self._apply(app, o, v.strip()))

    def _coerce(self, o: Dict[str, Any], v: Any) -> Any:
        t = o["type"]
        if t == "int":
            return int(v)
        if t == "float":
            return float(v)
        if t == "list":
            return [x.strip() for x in str(v).split(",") if x.strip()]
        return v

    def _apply(self, app, o: Dict[str, Any], v: Any, remove: bool = False) -> None:
        from research.util import ResearchError
        from .widgets import Confirm
        try:
            val = None if remove else self._coerce(o, v)
            pv = app.call("settings_preview", changes={o["key"]: val}, scope="project", remove=remove)
        except (ValueError, ResearchError) as e:
            return app.notify(f"Not changed: {e}", severity="error")
        if pv.get("errors"):
            return app.notify("Not changed: " + "; ".join(f"{k}: {m}" for k, m in pv["errors"].items()), severity="error")
        if not pv.get("changed"):
            return app.notify("Already set to that value.")

        def go(ok: bool) -> None:
            if not ok:
                return
            if pv.get("policy_keys") or pv.get("risky_keys"):
                return app._with_pin(lambda pin: self._commit(app, o, val, remove, pin))
            self._commit(app, o, val, remove, None)
        app.push_screen(Confirm(("Reset " if remove else "Set ") + f"{o['key']}" + ("" if remove else f" to {short(val, 40)}") + "?\n\n" + (pv.get("diff") or "")[:900]), go)

    def _commit(self, app, o, val, remove, pin) -> None:
        if app._do("settings_apply", changes={o["key"]: val}, scope="project", confirm=True, remove=remove, pin=pin):
            app._settings_cache = None
            app.refresh_data()


SECTIONS: Dict[str, Section] = {s.table: s for s in (Work(), Agents(), Guide(), Settings())}
