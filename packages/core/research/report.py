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
    b.add("h3", f"{fid} · {f['title']} [{f['status']}]")
    b.add("quote", f.get("statement"))
    b.add("p", f"_{status} · by {who}{' · ' + review if review else ''}_")
    ev = [f"{x['id']} {x.get('title') or ''}".strip() for x in f["evidence"]["supports"]]
    b.add("p", "**Evidence:** " + "; ".join(ev) if ev else "**Evidence:** none linked")
    if f["evidence"]["contradicts"]:
        b.add("p", "**Contradicting evidence:** " + "; ".join(x["id"] for x in f["evidence"]["contradicts"]))
    b.add("p", f"**Limitations:** {f['limitations']}" if f.get("limitations") else None)
    _evidence_block(b, fid)
    arts = list(f["artifacts"])
    if not any(a.get("type") in _IMG_TYPES for a in arts):  # fall back to plots of the supporting runs
        from .runs import list_artifacts
        for x in f["evidence"]["supports"]:
            if x["type"] == "run":
                arts += list_artifacts(b.p, run=x["id"])
    b.images(arts, 3)


def _evidence_block(b: _Builder, fid: str) -> None:
    """Numbers bound to recorded evidence, re-verified now. Legacy/qualitative findings add nothing."""
    from .evidence import list_bindings, verify_finding
    v = verify_finding(b.p, fid)
    bds = {y["id"]: y for y in list_bindings(b.p, fid)}
    if not v["bindings"]:
        return
    rows = []
    for x in v["bindings"]:
        bd = bds[x["id"]]
        s = bd["source"]
        src = (f"{s['run']} · {s['metric']}" + (f"@{s['step']}" if s.get("step") is not None else "")) if bd["kind"] == "metric" else \
            f"{s['op']}(" + ", ".join(f"{i['run']}:{i['metric']}" for i in s["inputs"]) + ")"
        part = bd.get("partition") or "—"
        a = (x.get("artifact") or {}).get("id") or (bd.get("artifact") or {}).get("id") or "—"
        rows.append([_fmt(x["claimed"]) + (f" {x['unit']}" if x.get("unit") else ""), src, a, part, x["status"].upper(),
                     "; ".join(x["issues"] + x["warnings"]) or "—"])
    b.add("table", (["Value", "Source (run · metric / calculation)", "Artifact", "Partition", "Verification", "Notes"], rows))
    b.add("p", "_Numbers are re-checked against the recorded evidence when this report is built. Verified numbers say nothing "
               "about whether the interpretation is right; see the review status above._")


def _glance(p: Project, scope: Optional[str]) -> List[Tuple[str, str, str]]:
    """The numbers a reader wants first: (label, value, tone). Counts come straight from the records; with a scope they cover that scope."""
    fids = _findings_in_scope(p, scope)
    fs = [p.get("finding", f) for f in fids]
    if not scope:
        exps = [r["id"] for r in p.q("SELECT id FROM experiments ORDER BY id")]
    elif type_of(scope) == "question":
        exps = [r["id"] for r in p.q("SELECT id FROM experiments WHERE question_id=? ORDER BY id", (scope,))]
    else:
        exps = _experiments_in_scope(p, scope)
    es = [p.get("experiment", e) for e in exps]
    runs = [r for e in exps for r in p.q("SELECT status FROM runs WHERE experiment_id=?", (e,))]
    n = lambda xs, st: sum(1 for x in xs if x["status"] == st)  # noqa: E731
    tiles = [("Experiments", f"{len(es)}", ""), ("Runs", f"{len(runs)}" + (f" ({n(runs, 'failed')} failed)" if n(runs, "failed") else ""), "bad" if n(runs, "failed") else ""),
             ("Findings supported", f"{n(fs, 'supported')} of {len(fs)}", "ok" if fs and n(fs, "supported") else ""),
             ("Still preliminary", f"{n(fs, 'preliminary')}", "warn" if n(fs, "preliminary") else ""),
             ("Contradicted", f"{n(fs, 'contradicted')}", "bad" if n(fs, "contradicted") else "")]
    if not scope:
        tiles.append(("Open questions", f"{p.q1('SELECT COUNT(*) AS n FROM questions WHERE status NOT IN (?,?)', ('answered', 'closed'))['n']}", ""))
    return tiles


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
    b.add("glance", _glance(p, scope))
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

def _slug(text: str, seen: Dict[str, int]) -> str:
    """GitHub-style heading id, unique within one document."""
    base = re.sub(r"[^\w\- ]", "", re.sub(r"`", "", str(text)).lower()).strip().replace(" ", "-") or "section"
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def to_markdown(blocks: List[Block]) -> str:
    L: List[str] = []
    h2s = [d for k, d in blocks if k == "h2"]
    seen: Dict[str, int] = {}
    anchors = {d: _slug(d, seen) for k, d in blocks if k == "h2"}
    for kind, d in blocks:
        if kind == "glance":
            L += ["| " + " | ".join(t[0] for t in d) + " |", "|" + "---|" * len(d), "| " + " | ".join(f"**{t[1]}**" for t in d) + " |", ""]
            if h2s:
                L += ["**Contents:** " + " · ".join(f"[{re.sub(r'[*_`]', '', h)}](#{anchors[h]})" for h in h2s), ""]
        elif kind in ("h1", "h2", "h3", "h4"):
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


_BADGE = re.compile(r"\[(supported|preliminary|contradicted|superseded|completed|failed|running|open|active|done|blocked|answered|closed|"
                    r"abandoned|baseline)(, [^\]]*)?\]")
_CELLTONE = {"VERIFIED": "ok", "MISMATCH": "bad", "MISSING": "bad", "STALE": "warn", "UNVERIFIABLE": "warn", "completed": "ok",
             "failed": "bad", "running": "info", "supported": "ok", "contradicted": "bad", "preliminary": "warn"}


def _inline(s: str) -> str:
    s = _html.escape(str(s))
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<i>\1</i>", s)
    return _BADGE.sub(lambda m: f'<span class="badge b-{m.group(1)}">{m.group(1)}{m.group(2) or ""}</span>', s)


_CSS = """
:root{--fg:#1f2328;--mut:#57606a;--bg:#fff;--card:#f6f8fa;--line:#d0d7de;--acc:#0969da;--ok:#1a7f37;--warn:#9a6700;--bad:#cf222e;--info:#0969da}
@media (prefers-color-scheme:dark){:root{--fg:#e6edf3;--mut:#8b949e;--bg:#0d1117;--card:#161b22;--line:#30363d;--acc:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--info:#58a6ff}}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;color:var(--fg);background:var(--bg)}
.wrap{display:grid;grid-template-columns:minmax(0,1fr);max-width:1180px;margin:0 auto;padding:24px 20px 80px;gap:32px}
@media (min-width:1000px){.wrap{grid-template-columns:230px minmax(0,1fr)}}
nav.toc{font-size:14px;position:sticky;top:16px;align-self:start;max-height:calc(100vh - 32px);overflow:auto}
nav.toc b{display:block;margin-bottom:6px;color:var(--mut);text-transform:uppercase;font-size:11px;letter-spacing:.06em}
nav.toc ul{list-style:none;margin:0;padding:0}nav.toc li{margin:3px 0}nav.toc li.l3{padding-left:14px;font-size:13px}
nav.toc a{color:var(--fg);text-decoration:none}nav.toc a:hover{color:var(--acc)}
@media (max-width:999px){nav.toc{position:static;border:1px solid var(--line);border-radius:8px;padding:10px 14px;background:var(--card)}}
main{min-width:0}h1{font-size:30px;line-height:1.25;margin:0 0 6px}
h2{font-size:22px;margin:42px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}h3{font-size:18px;margin:28px 0 8px}h4{font-size:16px;margin:20px 0 6px}
h2 a.anchor,h3 a.anchor,h4 a.anchor{color:var(--mut);text-decoration:none;margin-left:6px;opacity:0}h2:hover a.anchor,h3:hover a.anchor,h4:hover a.anchor{opacity:1}
p{margin:8px 0}ul{padding-left:22px}li{margin:4px 0}
blockquote{margin:10px 0;padding:8px 16px;border-left:4px solid var(--acc);background:var(--card);border-radius:0 6px 6px 0}
code{background:var(--card);padding:1px 5px;border-radius:4px;font-size:.9em}
.tbl-wrap{overflow-x:auto;margin:12px 0;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;font-size:13.5px;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}td:last-child{white-space:normal}
th{background:var(--card);position:sticky;top:0;font-weight:600}tr:last-child td{border-bottom:0}
td.t-ok{color:var(--ok);font-weight:600}td.t-bad{color:var(--bad);font-weight:600}td.t-warn{color:var(--warn);font-weight:600}td.t-info{color:var(--info);font-weight:600}
.glance{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:18px 0 8px}
.tile{border:1px solid var(--line);border-radius:10px;padding:10px 14px;background:var(--card)}
.tile span{display:block;font-size:12px;color:var(--mut)}.tile b{font-size:21px;font-variant-numeric:tabular-nums}
.tile.ok b{color:var(--ok)}.tile.warn b{color:var(--warn)}.tile.bad b{color:var(--bad)}
.badge{display:inline-block;font-size:11.5px;font-weight:600;padding:1px 8px;border-radius:99px;border:1px solid var(--line);background:var(--card);vertical-align:1px;white-space:nowrap}
.b-supported,.b-completed,.b-done,.b-answered{color:var(--ok);border-color:var(--ok)}.b-preliminary,.b-open,.b-active,.b-blocked{color:var(--warn);border-color:var(--warn)}
.b-contradicted,.b-failed,.b-abandoned{color:var(--bad);border-color:var(--bad)}.b-running,.b-baseline{color:var(--info);border-color:var(--info)}
.figs{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin:12px 0}
figure{margin:0}figure img{width:100%;height:auto;max-height:340px;object-fit:contain;background:#fff;border:1px solid var(--line);border-radius:8px}
figcaption{font-size:12.5px;color:var(--mut);margin-top:4px}
@media print{nav.toc{display:none}.wrap{display:block;max-width:none;padding:0}h2{break-after:avoid}figure,.tbl-wrap,blockquote{break-inside:avoid}}
"""


def to_html(title: str, blocks: List[Block]) -> str:
    body: List[str] = []
    toc: List[str] = []
    seen: Dict[str, int] = {}
    for kind, d in blocks:
        if kind in ("h1", "h2", "h3", "h4"):
            hid = _slug(d, seen)
            body.append(f'<{kind} id="{hid}">{_inline(d)}<a class="anchor" href="#{hid}" aria-label="Link to this section">#</a></{kind}>' if kind != "h1"
                        else f"<{kind}>{_inline(d)}</{kind}>")
            if kind in ("h2", "h3") and not (kind == "h3" and len([x for x in toc if 'class="l3"' in x]) > 40):
                toc.append(f'<li class="{"l3" if kind == "h3" else "l2"}"><a href="#{hid}">{_inline(d)}</a></li>')
        elif kind == "glance":
            body.append('<div class="glance">' + "".join(f'<div class="tile {t[2]}"><span>{_html.escape(t[0])}</span><b>{_html.escape(t[1])}</b></div>' for t in d) + "</div>")
        elif kind == "p":
            body.append(f"<p>{_inline(d)}</p>")
        elif kind == "quote":
            body.append(f"<blockquote>{_inline(d)}</blockquote>")
        elif kind == "ul":
            body.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in d) + "</ul>")
        elif kind == "table":
            hdr, rows = d

            def td(c: Any) -> str:
                tone = _CELLTONE.get(str(c).strip())
                return f'<td class="t-{tone}">{_inline(c)}</td>' if tone else f"<td>{_inline(c)}</td>"
            body.append('<div class="tbl-wrap"><table><thead><tr>' + "".join(f'<th scope="col">{_inline(h)}</th>' for h in hdr) + "</tr></thead><tbody>"
                        + "".join("<tr>" + "".join(td(c) for c in row) + "</tr>" for row in rows) + "</tbody></table></div>")
        elif kind == "img":
            body.append('<div class="figs">' + "".join(
                f'<figure><img loading="lazy" src="{_html.escape(src)}" alt="{_html.escape(cap)}"><figcaption>{_inline(cap)}</figcaption></figure>'
                for src, cap in d) + "</div>")
    nav = f'<nav class="toc" aria-label="Contents"><b>Contents</b><ul>{"".join(toc)}</ul></nav>' if toc else ""
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width,initial-scale=1\"><title>{_html.escape(title)}</title><style>{_CSS}</style>"
            f"</head><body><div class=\"wrap\">{nav}<main>{''.join(body)}</main></div></body></html>\n")


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
    return {"title": title, "paths": paths, "abs": [p.abspath(x) for x in paths]}
