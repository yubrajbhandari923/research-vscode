"""Terminal output helpers: colours, JSON mode, key/value and list printers."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from research.util import human_size




_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


bold = lambda s: _c("1", s)


dim = lambda s: _c("2", s)


green = lambda s: _c("32", s)


red = lambda s: _c("31", s)


yellow = lambda s: _c("33", s)


cyan = lambda s: _c("36", s)


magenta = lambda s: _c("35", s)


STATUS_COLOR = {
    "open": cyan, "investigating": yellow, "answered": green, "blocked": red, "abandoned": dim,
    "proposed": dim, "ready": cyan, "running": yellow, "needs_review": magenta, "completed": green, "failed": red,
    "queued": dim, "cancelled": dim, "unknown": red,
    "preliminary": yellow, "supported": green, "contradicted": red, "superseded": dim,
    "active": green, "reversed": dim,
    # Plan/Task statuses (Phase 0)
    "todo": dim, "verify": magenta, "done": green,
}


def st(s: Optional[str]) -> str:
    if not s:
        return ""
    return STATUS_COLOR.get(s, lambda x: x)(s)


def author(r: Dict[str, Any]) -> str:
    at = r.get("author_type")
    if at == "agent":
        return magenta("agent" + (f":{r['author_name']}" if r.get("author_name") else ""))
    return dim(at or "")


def out_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def section(title: str) -> None:
    print("\n" + bold(title))


def kv(label: str, value: Any, width: int = 16) -> None:
    if value in (None, "", [], {}):
        return
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        value = ", ".join(f"{k}={v}" for k, v in value.items())
    text = str(value).rstrip()
    lines = text.splitlines() or [""]
    print(f"  {dim(label.ljust(width - 1))} {lines[0]}")  # always at least one space after the label
    for l in lines[1:]:
        print(" " * (width + 2 + max(0, len(label) - width + 1)) + l)


def fmt_dur(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def fmt_val(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return "—" if v is None else str(v)


def _experiment_flags(s: Dict[str, Any]) -> List[str]:
    flag = []
    if s["unsynthesized"]:
        flag.append(yellow(f"⚠ {s['unsynthesized']} unsynthesized runs"))
    if s["over_run_budget"]:
        flag.append(red("run budget reached — synthesize before running more"))
    if s["over_failure_budget"]:
        flag.append(red(f"{s['unreviewed_failed']} failed runs need review"))
    return flag


def _metric_cell(r: Dict[str, Any], k: str):
    m = r["metrics"].get(k) or {}
    return fmt_val(m.get("value") if m.get("value") is not None else m.get("text"))


def _print_runs_table(e: Dict[str, Any]) -> None:
    section(f"Runs ({len(e['runs'])})")
    pn, mn = e["run_param_names"], e["metric_names"]
    hdr = ["run", "status", "exit", "time"] + pn + mn + ["label"]
    rows = [[r["id"], r["status"], fmt_val(r.get("exit_code")), fmt_dur(r.get("duration_s"))]
            + [fmt_val((r.get("parameters") or {}).get(k)) for k in pn] + [_metric_cell(r, k) for k in mn] + [r.get("label") or ""]
            for r in e["runs"]]
    widths = [max(len(str(h)), *(len(str(x[i])) for x in rows)) for i, h in enumerate(hdr)]
    print("  " + "  ".join(dim(str(h).ljust(w)) for h, w in zip(hdr, widths)))
    for row in rows:
        cells = [str(x).ljust(w) for x, w in zip(row, widths)]
        cells[1] = st(row[1]) + " " * (widths[1] - len(row[1]))
        print("  " + "  ".join(cells))


def _print_experiment_tail(e: Dict[str, Any]) -> None:
    if e["artifacts"]:
        section(f"Artifacts ({len(e['artifacts'])})")
        for a in e["artifacts"]:
            print(f"  {a['id']}  {a['path']}  {dim(a['type'])} {dim(human_size(a.get('size')))}"
                  + ("" if a["exists"] else red(" (missing)")) + (f"  — {a['description']}" if a.get("description") else ""))
    for syn in e["syntheses"]:
        section(f"Synthesis {syn['id']} · {syn['created_at'][:16]} · {author(syn)}")
        for key, label in (("what_happened", "happened"), ("what_worked", "worked"), ("what_failed", "failed"),
                           ("interpretation", "interpretation"), ("limitations", "limitations"),
                           ("unresolved", "unresolved"), ("next_experiment", "next")):
            kv(label, syn.get(key))
    if e["findings"]:
        section("Findings")
        for f in e["findings"]:
            print(f"  {f['id']}  {f['title']}  [{st(f['status'])}]")


def print_experiment(e: Dict[str, Any]) -> None:
    print(f"{bold(e['id'])}  {bold(e['title'])}  [{st(e['status'])}]" + (green("  ★ baseline") if e.get("is_baseline") else ""))
    if e.get("question"):
        kv("question", f"{e['question']['id']} {e['question']['title']}")
    if e.get("parent"):
        kv("derived from", f"{e['parent']['id']} {e['parent']['title']}")
    if e.get("param_delta"):
        kv("delta", ", ".join(f"{k}: {d.get('from')} → {d.get('to')}" for k, d in e["param_delta"].items()))
    for key, label in (("hypothesis", "hypothesis"), ("motivation", "motivation"), ("method", "method"),
                       ("expected_outcome", "expected"), ("success_criteria", "success"), ("stop_conditions", "stop"),
                       ("parameters", "parameters"), ("metrics_requested", "metrics"),
                       ("expected_artifacts", "exp. artifacts"), ("planned_runs", "planned runs"),
                       ("limitations", "limitations"), ("notes", "notes")):
        kv(label, e.get(key))
    kv("git", f"{e.get('git_branch') or '—'} @ {(e.get('git_commit') or '—')[:10]}" + (yellow(" (dirty)") if e.get("git_dirty") else ""))
    kv("author", author(e))
    flag = _experiment_flags(e["state"])
    if flag:
        print("  " + " · ".join(flag))
    if e["runs"]:
        _print_runs_table(e)
    _print_experiment_tail(e)


def print_finding(f: Dict[str, Any]) -> None:
    kind = red("✗ failed direction") if f.get("kind") == "failure" else ""
    print(f"{bold(f['id'])}  {bold(f['title'])}  [{st(f['status'])}] {kind}")
    kv("statement", f.get("statement"))
    kv("confidence", f.get("confidence"))
    for k in ("supports", "contradicts", "related"):
        ev = f["evidence"][k]
        if ev:
            kv(k, "\n".join(f"{b['id']} {b['title']}" + (red(" (missing)") if b.get("missing") else "") for b in ev))
    kv("questions", [q["id"] for q in f["questions"]])
    kv("limitations", f.get("limitations"))
    kv("contradicting", f.get("contradicting_evidence"))
    if f.get("superseded_by"):
        kv("superseded by", f["superseded_by"])
    kv("decisions", [f"{d['id']} {d['title']}" for d in f["decisions"]])
    kv("author", author(f))


def print_generic(d: Dict[str, Any]) -> None:
    title = d.get("title") or d.get("name") or d.get("statement") or ""
    print(f"{bold(d.get('id', ''))}  {bold(str(title))}  [{st(d.get('status'))}]")
    skip = {"id", "title", "type", "events", "links", "status"}
    for k, v in d.items():
        if k in skip or v in (None, "", [], {}):
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            v = [f"{x.get('id', '')} {x.get('title') or x.get('name') or x.get('path') or ''}".strip() for x in v]
        if isinstance(v, dict) and all(isinstance(x, (list, dict, type(None))) for x in v.values()):
            v = json.dumps(v, default=str)
        kv(k.replace("_", " "), v)


def print_list(rows: List[Dict[str, Any]], cols: List[str]) -> None:
    if not rows:
        print(dim("(none)"))
        return
    for r in rows:
        parts = []
        for c in cols:
            v = r.get(c)
            if c == "status":
                parts.append(f"[{st(v)}]")
            elif c == "author_type":
                if v == "agent":
                    parts.append(author(r))
            elif v not in (None, ""):
                parts.append(bold(str(v)) if c == "id" else str(v))
        print("  ".join(parts))


def _done(a, obj: Any, printer=None, msg: Optional[str] = None) -> None:
    if a.json:
        out_json(obj)
        return
    if msg:
        print(green("✓ ") + msg)
    for w in (obj or {}).get("warnings", []) if isinstance(obj, dict) else []:
        print(yellow("⚠ " + w))
    if printer:
        printer(obj)
