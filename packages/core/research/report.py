"""`research report`: a readable summary of what the project (or one question / plan / experiment) has learned,
with the plots and numbers that support it. Written to .research/reports/<scope>.md (and .html).

Structure: goal & current understanding → findings (with review status and evidence plots) → failed directions →
decisions → per question: experiments (design, runs table, synthesis, plots) → plans → open questions.
Images are linked relative to the report file, never copied.
"""
from __future__ import annotations

import html as _html
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import gitinfo
from .schema import normalize_id, type_of
from .services import experiment_state, list_syntheses
from .store import Project, _atomic_write
from .util import now_iso

Block = Tuple[str, Any]
_IMG_TYPES = ("image",)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return "—" if v is None else str(v)


class _Builder:
    def __init__(self, p: Project, out_dir: str):
        self.p, self.out_dir, self.blocks = p, out_dir, []  # type: ignore[var-annotated]

    def add(self, kind: str, data: Any) -> None:
        if data not in (None, "", []):
            self.blocks.append((kind, data))

    def rel(self, abspath: str) -> str:
        return os.path.relpath(abspath, self.out_dir).replace(os.sep, "/")

    def images(self, arts: List[Dict[str, Any]], limit: int) -> None:
        imgs = [(self.rel(a["abspath"]), f"{a['id']} · {a.get('description') or a['name']}")
                for a in arts if a.get("type") in _IMG_TYPES and a.get("exists") and a.get("abspath")][:limit]
        self.add("img", imgs)


def _findings_in_scope(p: Project, scope: Optional[str]) -> List[str]:
    from .views import experiment_detail, question_detail
    if not scope:
        return [r["id"] for r in p.q("SELECT id FROM findings WHERE status != 'superseded' ORDER BY "
                                     "CASE status WHEN 'supported' THEN 0 WHEN 'preliminary' THEN 1 ELSE 2 END, id")]
    t = type_of(scope)
    if t == "question":
        return [f["id"] for f in question_detail(p, scope)["findings"]]
    if t == "experiment":
        return [f["id"] for f in experiment_detail(p, scope)["findings"]]
    ids: List[str] = []
    for q in _questions_in_scope(p, scope):
        ids += [f["id"] for f in question_detail(p, q)["findings"]]
    for e in _experiments_in_scope(p, scope):
        ids += [f["id"] for f in experiment_detail(p, e)["findings"]]
    return list(dict.fromkeys(ids))


def _questions_in_scope(p: Project, scope: Optional[str]) -> List[str]:
    if not scope:
        return [r["id"] for r in p.q("SELECT id FROM questions WHERE parent_id IS NULL ORDER BY id")]
    t = type_of(scope)
    if t == "question":
        return [scope]
    if t == "plan":
        plan = p.get("plan", scope)
        qs = [plan["root_question_id"]] if plan.get("root_question_id") else []
        qs += [r["related_question_id"] for r in p.q("SELECT related_question_id FROM tasks WHERE plan_id=? AND "
                                                     "related_question_id IS NOT NULL", (scope,))]
        return list(dict.fromkeys(qs))
    return []


def _experiments_in_scope(p: Project, scope: Optional[str]) -> List[str]:
    if scope and type_of(scope) == "experiment":
        return [scope]
    if scope and type_of(scope) == "plan":
        return [r["related_experiment_id"] for r in p.q("SELECT DISTINCT related_experiment_id FROM tasks WHERE plan_id=? "
                                                        "AND related_experiment_id IS NOT NULL", (scope,))]
    return []


def _experiment_section(b: _Builder, eid: str, level: str = "h3") -> None:
    from .views import experiment_detail
    e = experiment_detail(b.p, eid)
    st = experiment_state(b.p, eid)
    b.add(level, f"{e['id']} · {e['title']} [{e['status']}{', baseline' if e.get('is_baseline') else ''}]")
    b.add("p", f"**Hypothesis:** {e['hypothesis']}" if e.get("hypothesis") else None)
    b.add("p", f"**Success criteria:** {e['success_criteria']}" if e.get("success_criteria") else None)
    if e["runs"]:
        pn, mn = e["run_param_names"], e["metric_names"]
        rows = []
        for r in e["runs"][:20]:
            rows.append([r["id"], r["status"]] + [_fmt((r.get("parameters") or {}).get(k)) for k in pn]
                        + [_fmt((r["metrics"].get(k) or {}).get("value") if (r["metrics"].get(k) or {}).get("value") is not None
                                else (r["metrics"].get(k) or {}).get("text")) for k in mn])
        b.add("table", (["run", "status"] + pn + mn, rows))
        if len(e["runs"]) > 20:
            b.add("p", f"_… {len(e['runs']) - 20} more runs (`research experiment show {eid}`)_")
    else:
        b.add("p", "_No runs yet._")
    syn = list_syntheses(b.p, eid)
    if syn:
        s = syn[-1]
        items = [f"**{label}:** {s[k]}" for k, label in (("what_happened", "What happened"), ("interpretation", "Interpretation"),
                                                          ("what_failed", "What failed"), ("next_experiment", "Next"))
                 if s.get(k)]
        b.add("ul", items)
    elif st["unsynthesized"]:
        b.add("p", f"_{st['unsynthesized']} runs not yet synthesized._")
    b.images(e["artifacts"], 4)


def _finding_section(b: _Builder, fid: str) -> None:
    from .verification import latest
    from .views import finding_detail
    f = finding_detail(b.p, fid)
    rev = latest(b.p, fid, "review")
    status = f["status"] + (f", {f['confidence']} confidence" if f.get("confidence") else "")
    who = f.get("author_name") or f.get("author_type")
    review = (f"reviewed by {rev['author_name'] or rev['author_type']}: {rev['verdict'].replace('_', ' ')}" if rev
              else ("not yet independently reviewed" if f.get("author_type") == "agent" else ""))
    b.add("h3", f"{fid} · {f['title']}")
    b.add("quote", f.get("statement"))
    b.add("p", f"_{status} · by {who}{' · ' + review if review else ''}_")
    ev = [f"{x['id']} {x.get('title') or ''}".strip() for x in f["evidence"]["supports"]]
    b.add("p", "**Evidence:** " + "; ".join(ev) if ev else "**Evidence:** none linked")
    if f["evidence"]["contradicts"]:
        b.add("p", "**Contradicting evidence:** " + "; ".join(x["id"] for x in f["evidence"]["contradicts"]))
    b.add("p", f"**Limitations:** {f['limitations']}" if f.get("limitations") else None)
    arts = list(f["artifacts"])
    if not any(a.get("type") in _IMG_TYPES for a in arts):  # fall back to plots of the supporting runs
        from .runs import list_artifacts
        for x in f["evidence"]["supports"]:
            if x["type"] == "run":
                arts += list_artifacts(b.p, run=x["id"])
    b.images(arts, 3)


def build(p: Project, scope: Optional[str] = None, out_dir: Optional[str] = None) -> Tuple[str, List[Block]]:
    from .context import resume_context
    scope = normalize_id(scope) if scope else None
    out_dir = out_dir or p.path("reports")
    b = _Builder(p, out_dir)
    r = resume_context(p)
    g = gitinfo.info(p.root)
    title = p.name if not scope else f"{p.name} — {scope} {(b.p.get(type_of(scope), scope) or {}).get('title', '')}".strip()
    b.add("h1", f"Research report: {title}")
    b.add("p", f"_Generated {now_iso()[:16].replace('T', ' ')}"
          + (f" · git `{g['branch']}` @ `{(g['commit'] or '')[:10]}`" if g.get("branch") else "") + "_")
    b.add("p", f"**Goal:** {r['project']['goal']}" if r["project"].get("goal") else None)
    cp = r["latest_checkpoint"]
    if cp and not scope:
        b.add("h2", "Where things stand")
        b.add("ul", [f"**{label}:** {cp[k]}" for k, label in (("understanding", "Current understanding"),
                                                              ("current_problem", "Open problem"), ("next_experiment", "Next step"))
                     if cp.get(k)] + ([f"**Baseline:** {r['baseline']['id']} — {r['baseline']['title']}"] if r.get("baseline") else []))

    fids = _findings_in_scope(p, scope)
    results = [f for f in fids if p.get("finding", f)["kind"] == "result" and p.get("finding", f)["status"] != "contradicted"]
    failures = [f for f in fids if f not in results]
    b.add("h2", "What we learned")
    if results:
        for f in results:
            _finding_section(b, f)
    else:
        b.add("p", "_No findings recorded yet._")
    if failures:
        b.add("h2", "Failed directions and contradicted claims")
        b.add("ul", [f"**{f}** {p.get('finding', f)['title']} [{p.get('finding', f)['status']}]"
                     + (f" — {p.get('finding', f)['limitations']}" if p.get('finding', f).get("limitations") else "")
                     for f in failures])
    if not scope and r["decisions"]:
        b.add("h2", "Decisions in force")
        b.add("ul", [f"**{d['id']}** ({d['date']}) {d['statement']}" + (f" — based on {', '.join(d['findings'])}" if d["findings"] else "")
                     for d in r["decisions"]])

    qids = _questions_in_scope(p, scope)
    if qids:
        b.add("h2", "Questions and experiments")
        for qid in qids:
            q = p.get("question", qid)
            b.add("h3", f"{qid} · {q['title']} [{q['status']}]")
            b.add("p", q.get("description"))
            for e in p.q("SELECT id FROM experiments WHERE question_id=? ORDER BY id", (qid,)):
                _experiment_section(b, e["id"], "h4")
    for eid in _experiments_in_scope(p, scope):
        if not any(p.q("SELECT 1 FROM experiments WHERE id=? AND question_id=?", (eid, q)) for q in qids):
            _experiment_section(b, eid, "h3")
    orphans = [] if scope else [e["id"] for e in p.q("SELECT id FROM experiments WHERE question_id IS NULL ORDER BY id")]
    if orphans:
        b.add("h3", "Experiments without a question")
        for eid in orphans:
            _experiment_section(b, eid, "h4")

    from . import plans as P
    plans = [P.get_plan_with_tasks(p, scope)] if scope and type_of(scope) == "plan" else (
        [] if scope else [P.get_plan_with_tasks(p, x["id"]) for x in P.list_plans(p)])
    if plans:
        b.add("h2", "Plans")
        for pl in plans:
            b.add("h3", f"{pl['id']} · {pl['title']} [{pl['status']}] — {pl['tasks_done']}/{pl['task_count']} tasks done")
            b.add("p", pl.get("objective"))
            b.add("ul", [f"{t['id']} [{t['status']}] {t['title']}" + (f" — {t['result']}" if t.get("result") else "")
                         for t in pl["tasks"]])
    if not scope:
        open_q = r["active_questions"] + r["open_questions"] + r["blocked_questions"]
        if open_q:
            b.add("h2", "Open questions")
            b.add("ul", [f"**{q['id']}** {q['title']} [{q['status']}]" for q in open_q])
    return title, b.blocks


# =============================================================================== rendering

def to_markdown(blocks: List[Block]) -> str:
    L: List[str] = []
    for kind, d in blocks:
        if kind in ("h1", "h2", "h3", "h4"):
            L += ["#" * int(kind[1]) + " " + d, ""]
        elif kind == "p":
            L += [d, ""]
        elif kind == "quote":
            L += ["> " + d.replace("\n", "\n> "), ""]
        elif kind == "ul":
            L += [f"- {x}" for x in d] + [""]
        elif kind == "table":
            hdr, rows = d
            L += ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
            L += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows] + [""]
        elif kind == "img":
            L += ["  ".join(f"![{cap}]({src})" for src, cap in d), ""]
    return "\n".join(L).rstrip() + "\n"


def _inline(s: str) -> str:
    s = _html.escape(str(s))
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<i>\1</i>", s)


def to_html(title: str, blocks: List[Block]) -> str:
    body: List[str] = []
    for kind, d in blocks:
        if kind in ("h1", "h2", "h3", "h4"):
            body.append(f"<{kind}>{_inline(d)}</{kind}>")
        elif kind == "p":
            body.append(f"<p>{_inline(d)}</p>")
        elif kind == "quote":
            body.append(f"<blockquote>{_inline(d)}</blockquote>")
        elif kind == "ul":
            body.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in d) + "</ul>")
        elif kind == "table":
            hdr, rows = d
            body.append("<table><thead><tr>" + "".join(f"<th>{_inline(h)}</th>" for h in hdr) + "</tr></thead><tbody>"
                        + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in rows)
                        + "</tbody></table>")
        elif kind == "img":
            body.append('<div class="figs">' + "".join(
                f'<figure><img src="{_html.escape(src)}" alt=""><figcaption>{_inline(cap)}</figcaption></figure>'
                for src, cap in d) + "</div>")
    css = """body{font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:960px;margin:32px auto;
padding:0 20px;color:#1f2328;background:#fff}h1{font-size:26px}h2{margin-top:36px;border-bottom:1px solid #d0d7de;
padding-bottom:4px}blockquote{margin:8px 0;padding:6px 14px;border-left:4px solid #0969da;background:#f6f8fa}
table{border-collapse:collapse;font-size:13px;margin:8px 0}th,td{border:1px solid #d0d7de;padding:3px 8px;text-align:left}
th{background:#f6f8fa}code{background:#f6f8fa;padding:1px 4px;border-radius:4px}.figs{display:flex;flex-wrap:wrap;gap:12px}
figure{margin:0;max-width:300px}figure img{max-width:300px;max-height:240px;border:1px solid #d0d7de;border-radius:4px}
figcaption{font-size:12px;color:#57606a}
@media (prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}blockquote,th,code{background:#161b22}
th,td,h2,figure img{border-color:#30363d}figcaption{color:#8b949e}}"""
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width,initial-scale=1\"><title>{_html.escape(title)}</title><style>{css}</style>"
            f"</head><body>{''.join(body)}</body></html>\n")


def write_report(p: Project, scope: Optional[str] = None, fmt: str = "md", out: Optional[str] = None) -> Dict[str, Any]:
    """Write the report. fmt: md · html · both. Returns {paths, title}."""
    out = os.path.abspath(out) if out else None
    out_dir = os.path.dirname(out) if out else p.path("reports")
    title, blocks = build(p, scope, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    stem = (normalize_id(scope) if scope else "project").replace("/", "-")
    paths = []
    if fmt in ("md", "both"):
        path = out if out and fmt == "md" else p.path("reports", f"{stem}.md")
        _atomic_write(path, to_markdown(blocks))
        paths.append(p.rel(path))
    if fmt in ("html", "both"):
        path = out if out and fmt == "html" else p.path("reports", f"{stem}.html")
        _atomic_write(path, to_html(title, blocks))
        paths.append(p.rel(path))
    p.event("project", scope, "report", f"Report written: {', '.join(paths)}")
    return {"title": title, "paths": paths}
