"""What the tabs and the status bar show, as plain functions from op results to rich Text. No widgets and no calls except `gather_status`."""
from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from rich.text import Text

from .widgets import badge, plain

SEP = " | " if plain() else "  │  "
PLATFORMS = {"tui": "the TUI", "vscode": "VS Code", "local": "the CLI", "cli": "the CLI", "extension": "VS Code"}


# ----------------------------------------------------------------------------------------------- small formatting helpers
def via_label(via: Any) -> str:
    """'chat:discord' -> 'Discord', 'tui' -> 'the TUI'."""
    s = str(via or "")
    if s.startswith("chat"):
        return s.partition(":")[2].capitalize() or "chat"
    return PLATFORMS.get(s, s or "?")


def when(ts: Any) -> str:
    """ISO timestamp or epoch -> 'HH:MM' today, 'Nov 08 14:02' otherwise (local time); anything unparseable is shown as given."""
    try:
        t = dt.datetime.fromtimestamp(float(ts)) if isinstance(ts, (int, float)) else dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError, OSError):
        return str(ts or "")
    return t.strftime("%H:%M") if t.date() == dt.datetime.now().date() else t.strftime("%b %d %H:%M")


def chat_origin(x: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Who asked for this from chat: {where, by, at}; None when it was not requested from chat. Reads the dict defensively (older cores differ)."""
    r = x.get("requested_from_chat")
    if not r:
        return None
    r = r if isinstance(r, dict) else {"via": r}
    return {"where": via_label(r.get("via") or r.get("platform") or "chat"), "by": str(r.get("by") or r.get("user") or "?"),
            "at": when(r.get("ts") or r.get("at") or "")}


def chat_first(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda x: 0 if x.get("requested_from_chat") else 1)   # stable: the rest keep their order


def approved_via(x: Dict[str, Any]) -> str:
    a = x.get("approved_via")
    if isinstance(a, dict) and str(a.get("method", "")).endswith("chat"):
        return f"via chat (authenticator), {via_label('chat:' + str(a.get('platform') or ''))} user {a.get('user_id', '?')}"
    return ""


def origin_cell(x: Dict[str, Any]) -> str:
    o = chat_origin(x)
    if o:
        return f"{o['where']}: {o['by']}"
    return "via chat" if approved_via(x) else ""


# ----------------------------------------------------------------------------------------------- proposals
def proposal_rows(proposals: List[Dict[str, Any]]) -> List[tuple]:
    return [(x["id"], x["id"], badge(x["status"]), str(x.get("text") or x.get("kind") or "")[:60], origin_cell(x)) for x in chat_first(proposals)]


def proposal_detail(x: Dict[str, Any]) -> Text:
    out = Text()
    o = chat_origin(x)
    if o:
        out.append(f"Requested from {o['where']} by {o['by']} at {o['at']}\n", style="bold" if plain() else "bold dark_orange")
        out.append("Chat cannot approve this one: press a here and enter your PIN.\n\n", style="dim")
    if approved_via(x):
        out.append(f"Approved {approved_via(x)}\n\n", style="bold" if plain() else "bold green")
    return out


# ----------------------------------------------------------------------------------------------- decisions
def decision_order(ds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Open first (asked-from-chat on top), then the closed ones, newest first."""
    open_ = chat_first([d for d in ds if d["status"] in ("open", "answering")])
    done = sorted([d for d in ds if d["status"] not in ("open", "answering")], key=lambda d: (d.get("answer") or {}).get("ts") or d.get("created") or "", reverse=True)
    return open_ + done


def decision_rows(ds: List[Dict[str, Any]]) -> List[tuple]:
    return [(d["id"], d["id"], badge(d["status"]), (d["subject"] or {}).get("id") or "", d["question"].replace("\n", " ")[:80]) for d in ds]


def answered_line(d: Dict[str, Any]) -> str:
    a = d.get("answer") or {}
    what = f"option {a.get('key')} ({a.get('label')})" if a.get("key") else "free text"
    return f"answered via {via_label(a.get('via'))} by {a.get('by') or '?'} at {when(a.get('ts'))}: {what}"


def option_line(i: int, o: Dict[str, Any]) -> Text:
    t = Text()
    t.append(f"{i}  ", style="bold").append(str(o["label"]), style="bold")
    if o.get("protected"):
        t.append("   needs your PIN", style="" if plain() else "bold dark_orange")
    for k, v in (("", o.get("consequence")), ("does: ", "; ".join(o.get("effect") or []))):
        if v:
            t.append(f"\n   {k}{v}", style="" if k == "" else "dim")
    return t


def decision_detail(d: Dict[str, Any]) -> Text:
    t = Text(d["question"], style="bold")
    subj = d.get("subject") or {}
    t.append(f"\n\n{d['id']}  ").append_text(badge(d["status"])).append(f"  about {subj.get('type') or 'project'} {subj.get('id') or ''}".rstrip()
                                                                       + f"  asked by {d.get('created_by') or '?'}\n", style="dim")
    o = chat_origin(d)
    if o:
        t.append(f"Chosen from {o['where']} by {o['by']} at {o['at']}: that option needs your PIN here.\n", style="bold" if plain() else "bold dark_orange")
    t.append("\n")
    for i, op in enumerate(d["options"], 1):
        t.append_text(option_line(i, op)).append("\n\n")
    if d.get("answer"):
        t.append(answered_line(d) + "\n", style="" if plain() else "green")
        t.append(f"-> {d['answer'].get('outcome') or ''}\n")
    elif d["status"] == "open":
        t.append(f"Press 1-{len(d['options'])} to choose, t to answer in your own words.", style="dim")
    return t


# ----------------------------------------------------------------------------------------------- status bar
def safe(fn: Callable[..., Any], *a: Any, default: Any = None, **kw: Any) -> Any:
    try:
        return fn(*a, **kw)
    except Exception:       # the bar never takes the TUI down: an op this core does not have, or a half-written file, just leaves its field out
        return default


def today_spend(records: List[Dict[str, Any]]) -> Optional[float]:
    """Provider-reported cost of today's usage records (local day); None when nothing reported a cost (never shown as zero)."""
    day, total = dt.datetime.now().date(), None
    for r in records:
        try:
            if dt.datetime.fromisoformat(str(r["ts"])).astimezone().date() != day:
                continue
        except (KeyError, ValueError):
            continue
        if isinstance(r.get("cost_usd"), (int, float)):
            total = (total or 0) + r["cost_usd"]
    return total


def plan_signals(call: Callable[..., Any], plans: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Running agents, plans with automatic promotion authorized, provider waits: one supervisor_status per managed plan."""
    out: Dict[str, Any] = {"running": 0, "unattended": [], "waiting": []}
    for pl in plans:
        s = safe(call, "supervisor_status", plan=pl["key"])
        if not s:
            continue
        out["running"] += int((s.get("concurrency") or {}).get("running") or 0)
        if (s.get("auto_promotion") or {}).get("authorized"):
            out["unattended"].append(pl["key"])
        w = s.get("waiting_provider") or (s.get("health") or {}).get("waiting_provider")   # added by the usage-limit work: absent -> no chip
        if isinstance(w, dict) and w.get("provider"):
            out["waiting"].append(w)
    return out


def gather_status(call: Callable[..., Any], plans: List[Dict[str, Any]], records: Callable[[], List[Dict[str, Any]]], open_decisions: Optional[int]) -> Dict[str, Any]:
    gw = safe(call, "chat_gateway", action="status") or {}
    users = safe(call, "chat_users", default=None)
    return {**plan_signals(call, plans), "spend": safe(lambda: today_spend(records())), "decisions": open_decisions,
            "gateway": {"known": bool(gw), "running": bool(gw.get("running")), "platforms": gw.get("platforms") or [], "paired": len(users or [])}}


def _chip(t: Text, text: str, style: str) -> None:
    if t.plain:
        t.append(SEP, style="dim")
    t.append(text, style="" if plain() else style)


def status_line(s: Dict[str, Any]) -> Text:
    """Most urgent first, so a narrow terminal that wraps still shows what needs you."""
    t, mark = Text(), (lambda a, b: a if plain() else b)
    if s.get("decisions"):
        _chip(t, f"{mark('*', '◆')} {s['decisions']} open decision{'s' if s['decisions'] != 1 else ''}", "bold dark_orange")
    for w in s["waiting"]:
        until = f" until {when(w['until'])}" if w.get("until") else ""
        _chip(t, f"{mark('~', '⏳')} waiting for {str(w['provider']).capitalize()}{until}", "bold dark_orange")
    if s["unattended"]:
        _chip(t, f"{mark('!', '⚠')} unattended: auto-promote on", "bold red")
    _chip(t, f"{s['running']} agent{'s' if s['running'] != 1 else ''} running", "bold blue" if s["running"] else "dim")
    _chip(t, "spend today " + (f"${s['spend']:.2f}" if s["spend"] is not None else "n/a"), "")
    g = s["gateway"]
    if g["running"]:
        who = "+".join(g["platforms"]) or "no platform"
        _chip(t, f"gateway {mark('on', '●')} {who}, {g['paired']} paired", "green")
    elif g["known"] and g["paired"]:
        _chip(t, f"gateway {mark('off', '○')} stopped, {g['paired']} paired", "dark_orange")
    else:
        _chip(t, "chat off" if g["known"] else "chat n/a", "dim")
    return t


# ----------------------------------------------------------------------------------------------- dashboard panels
def _row(t: Text, r: Dict[str, Any], tail: str) -> None:
    t.append(f"{r['id']} ", style="bold").append_text(badge(r["status"])).append(f"  {tail}\n")


def panel_status(runs: List[Dict[str, Any]]) -> Any:
    bar, t = "#" if plain() else "█", Text()
    by = Counter(r["status"] for r in runs)
    for st, n in by.most_common():
        t.append_text(badge(st)).append(f"  {n:3} ").append(bar * min(n, 20) + "\n", style="dim")
    return t if by else "No runs yet. Start one with `research run exec`."


def panel_runs(runs: List[Dict[str, Any]]) -> Any:
    t = Text()
    for r in sorted(runs, key=lambda r: (r["started_at"] or "", r["id"]), reverse=True)[:5]:
        _row(t, r, r["label"] or (r["command"] or "")[:30])
    return t if runs else "No runs yet."


def panel_approvals(proposals: List[Dict[str, Any]], decisions: Optional[int]) -> Text:
    pend = chat_first([x for x in proposals if x["status"] == "pending"])
    t = Text()
    t.append(f"{'! ' if plain() else '⚠ '}{len(pend)} pending" if pend else f"{'ok ' if plain() else '✓ '}nothing pending",
             style="" if plain() else "bold dark_orange" if pend else "green")
    for x in pend[:4]:
        o = origin_cell(x)
        t.append(f"\n{x['id']}  {str(x.get('text') or x.get('kind') or '')[:30]}" + (f"  [{o}]" if o else ""))
    t.append("\n\nTab 4: a approve, d reject" if pend else "")
    if decisions:
        t.append(f"\n{decisions} open decision{'s' if decisions != 1 else ''}: tab 5", style="" if plain() else "bold dark_orange")
    return t


def panel_findings(findings: List[Dict[str, Any]]) -> Any:
    t = Text()
    for f in findings[:5]:
        _row(t, f, f["title"])
    return t if findings else "No findings yet."


def panel_plans(sup: List[Dict[str, Any]]) -> Any:
    t = Text()
    for s in sup:
        t.append(f"{s['key']} ", style="bold").append_text(badge(s["state"])).append(f"  {s['progress']}  health={s['health']}\n")
    return t if sup else "None. Add a plan with `research plan yaml apply`."


def panel_system(T: Dict[str, Any], doc: Dict[str, Any], pin: str) -> Text:
    import research
    d = doc.get("summary", {})
    t = Text.assemble((T["project"]["name"] or "", "bold"), f"\n{T['project'].get('goal') or ''}\n",
                      f"questions {len(T['questions'])}  experiments {len(T['experiments'])}  plans {len(T['plans'])}\n"
                      f"decisions {len(T['decisions'])}  findings {len(T['findings'])}\ncore {research.__version__}  PIN {pin}\ndoctor: ")
    t.append_text(badge("ok" if doc.get("healthy") else "failed") if doc else Text("n/a"))
    t.append(f"  {d.get('errors', 0)} errors, {d.get('warnings', 0)} warnings" if doc else "")
    return t
