"""`research report --full`: the complete recorded research context as one deterministic Markdown document.

Zero LLM involvement: this module only retrieves, joins, orders and renders what Research OS already recorded.
Nothing is summarized, inferred or rewritten.

Determinism contract (see docs/SPEC.md "Full context export"):
  * the project is opened read-only (`Project(sync=False)`); rendering never writes, never logs an event
  * every list is ordered by explicit id/timestamp with documented tie-breakers
  * floats, timestamps, None and nested structures have one canonical spelling (`_num`, `_val`, `_json`)
  * paths are project-relative whenever the file lives inside the project
  * no wall-clock time appears unless `stamp=True` (explicitly non-reproducible mode)
Given the same canonical files and the same options, the bytes are identical.

Every emitted record carries a marker comment `<!-- research:record TYPE ID -->`, which lets `coverage()`
verify the rendered document against the snapshot *independently* of the exporter's own bookkeeping.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, gitinfo
from .schema import ENTITIES, normalize_id, type_of
from .store import Project, _atomic_write
from .util import ResearchError

EXPORT_SCHEMA = 1
MARKER = "research:record"
_MARKER_RE = re.compile(r"^<!-- " + MARKER + r" ([a-z_]+) (.+?) -->$", re.M)

# Record categories the coverage manifest accounts for, in report order.
CATEGORIES = ["question", "plan", "task", "experiment", "run", "metric", "artifact", "synthesis",
              "finding", "review", "decision", "checkpoint", "note", "source", "event"]

# The document's fixed section hierarchy. `render` emits exactly these as `## ` headings and `coverage`
# requires every one of them to be present, so a renamed or dropped section fails verification.
SECTIONS = ["1. Export Metadata", "2. Project Definition and Configuration", "3. Current Research State",
            "4. Questions and Hypotheses", "5. Plans and Tasks", "6. Experiments",
            "7. Findings and Reviews", "8. Decisions and Supersession History", "9. Checkpoints",
            "10. Research Notes", "11. Open Work and Unresolved Problems", "12. Audit / Event History",
            "13. Data Integrity and Missing Evidence", "14. Coverage Manifest"]


# =============================================================================== value formatting

def _num(v: Any) -> str:
    """One canonical spelling per number: ints bare, floats with repr (shortest round-trip, platform-stable)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "NaN"
        if v in (float("inf"), float("-inf")):
            return "Infinity" if v > 0 else "-Infinity"
        if v == int(v) and abs(v) < 1e16:
            return str(int(v)) + ".0"
        return repr(v)
    return str(v)


def _val(v: Any) -> str:
    """Render one field value for a Markdown table cell / inline text. `None` is always `—`."""
    if v is None or v == "" or v == [] or v == {}:
        return "—"
    if isinstance(v, (bool, int, float)):
        return _num(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(_val(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}={_val(v[k])}" for k in sorted(v))
    return str(v)


def _json(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False, indent=2, default=str)


def _cell(v: Any) -> str:
    """Table cell: escape pipes and flatten newlines so a multiline value cannot break the table."""
    return _val(v).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _block(text: Any) -> str:
    """A multi-line field rendered verbatim. Kept as-is (Markdown in records is the author's)."""
    s = "" if text is None else str(text)
    return s.rstrip("\n")


# =============================================================================== snapshot

def _run_record(p: Project, r: Dict[str, Any]) -> Dict[str, Any]:
    from .runs import metrics_for_run
    out = dict(r)
    # Metric record id = position in the run's ordered history. Positional (not the SQLite rowid) so the
    # export is unchanged by `research rebuild`, which reassigns rowids.
    out["metrics"] = [
        {"name": m["name"], "value": m["value"], "text": m["value_text"], "step": m["step"],
         "unit": m["unit"], "timestamp": m["timestamp"], "author_type": m["author_type"], "_n": n}
        for n, m in enumerate(metrics_for_run(p, r["id"]), 1)]
    out["artifacts"] = _artifacts(p, "SELECT * FROM artifacts WHERE run_id=? ORDER BY id", (r["id"],))
    rd = p.abspath(r.get("run_dir"))
    files = []
    if rd and os.path.isdir(rd):
        for fn in sorted(os.listdir(rd)):
            fp = os.path.join(rd, fn)
            if os.path.isfile(fp):
                files.append({"name": fn, "size": os.path.getsize(fp)})
    out["run_dir_files"] = files
    out["run_dir_exists"] = bool(rd and os.path.isdir(rd))
    return out


def _artifacts(p: Project, sql: str, args: tuple) -> List[Dict[str, Any]]:
    from .runs import _decode_artifact
    out = []
    for row in p.q(sql, args):
        a = _decode_artifact(p, row)
        out.append({k: a.get(k) for k in ("id", "run_id", "experiment_id", "name", "type", "path", "external",
                                          "description", "size", "mtime", "hash", "tags", "preview",
                                          "author_type", "author_name", "author_model", "created_at", "updated_at",
                                          "exists")})
    return out


def _unknown_fields(row: Dict[str, Any], known: List[str]) -> Dict[str, Any]:
    """Fields a future schema added that this renderer has no section for — preserved generically."""
    return {k: v for k, v in sorted(row.items())
            if k not in known and k not in ("type", "links") and v not in (None, "", [], {})}


def _raw_columns(p: Project, etype: str, id_: str) -> Dict[str, Any]:
    """The entity's row straight from SQLite.

    `Project.get` decodes only the columns `schema.ENTITIES` declares, so a column added by a newer schema
    would be invisible here. Reading the raw row keeps those values in the export instead of dropping them.
    """
    row = p.q1(f"SELECT * FROM {ENTITIES[etype]['table']} WHERE id=?", (id_,)) or {}
    return dict(row)


def snapshot(p: Project, scope: Optional[str] = None) -> Dict[str, Any]:
    """Collect everything in scope into plain data. Read-only: makes no writes and logs no events."""
    from . import plans as P
    from . import services as S
    from . import verification as V
    from .evidence import list_bindings, verify_finding
    from .runs import list_runs

    scope = normalize_id(scope) if scope else None
    stype = type_of(scope) if scope else None
    assert scope is not None or stype is None
    if stype not in (None, "question", "experiment", "plan"):
        raise ResearchError(f"--full supports the whole project or one Q-/EXP-/PLAN- id, not {scope}",
                            hint="research report --full [Q-001 | EXP-001 | PLAN-001]")

    # ---- scope resolution: which experiments / questions / plans are in scope
    all_exp = [r["id"] for r in p.q("SELECT id FROM experiments ORDER BY id")]
    all_q = [r["id"] for r in p.q("SELECT id FROM questions ORDER BY id")]
    all_plan = [r["id"] for r in p.q("SELECT id FROM plans ORDER BY id")]
    if stype is None:
        qids, eids, plan_ids = all_q, all_exp, all_plan
    elif stype == "question":
        qids = [scope] + [r["id"] for r in p.q("SELECT id FROM questions WHERE parent_id=? ORDER BY id", (scope,))]
        eids = [r["id"] for r in p.q("SELECT id FROM experiments WHERE question_id IN (%s) ORDER BY id"
                                     % ",".join("?" * len(qids)), tuple(qids))]
        plan_ids = [r["id"] for r in p.q("SELECT id FROM plans WHERE root_question_id=? ORDER BY id", (scope,))]
    elif stype == "experiment":
        e = p.get("experiment", str(scope))
        eids = [scope] + [r["id"] for r in p.q("SELECT id FROM experiments WHERE parent_id=? ORDER BY id", (scope,))]
        qids = [e["question_id"]] if e.get("question_id") else []
        plan_ids = []
    else:  # plan
        plan_ids = [scope]
        pl = p.get("plan", str(scope))
        qids = [pl["root_question_id"]] if pl.get("root_question_id") else []
        eids = sorted({r["related_experiment_id"] for r in p.q(
            "SELECT related_experiment_id FROM tasks WHERE plan_id=? AND related_experiment_id IS NOT NULL", (scope,))})
        qids = sorted(set(qids) | {r["related_question_id"] for r in p.q(
            "SELECT related_question_id FROM tasks WHERE plan_id=? AND related_question_id IS NOT NULL", (scope,))})
    qids = [q for q in dict.fromkeys(qids) if q and p.exists("question", q)]
    eids = [e for e in dict.fromkeys(eids) if e and p.exists("experiment", e)]
    plan_ids = [x for x in dict.fromkeys(plan_ids) if x and p.exists("plan", x)]

    snap: Dict[str, Any] = {
        "export_schema": EXPORT_SCHEMA, "tool_version": __version__,
        "source_schema_version": int(p.meta("schema_version", 0) or 0),
        "scope": scope or "project", "scope_type": stype or "project",
        "project": {
            "name": p.name, "root_basename": os.path.basename(p.root),
            "config": p.config, "git": gitinfo.info(p.root),
        },
    }

    # ---- questions
    snap["questions"] = []
    qcols = [c for c, _ in ENTITIES["question"]["columns"]] + ["id"]
    for qid in qids:
        q = p.get("question", qid)
        q["children"] = [r["id"] for r in p.q("SELECT id FROM questions WHERE parent_id=? ORDER BY id", (qid,))]
        q["experiments"] = [r["id"] for r in p.q("SELECT id FROM experiments WHERE question_id=? ORDER BY id", (qid,))]
        q["findings"] = sorted({l["src_id"] for l in p.links_to(qid) if l["src_type"] == "finding"})
        q["_unknown"] = _unknown_fields(_raw_columns(p, "question", qid), qcols)
        snap["questions"].append(q)

    # ---- plans and tasks
    snap["plans"] = []
    for plan_id in plan_ids:
        pl = P.get_plan_with_tasks(p, plan_id)
        for t in pl["tasks"]:
            t["checks_described"] = []
            for c in (t.get("checks") or []):
                try:
                    t["checks_described"].append(V.describe_check(V.parse_check(c)))
                except ResearchError as e:
                    t["checks_described"].append(f"(unparseable check: {e})")
            t["check_records"] = V.list_records(p, t["id"], "check")
            t["progress_notes"] = P.progress_notes(p, t["id"], limit=1000)
        snap["plans"].append(pl)

    # ---- experiments (with runs, metrics, artifacts, syntheses)
    snap["experiments"] = []
    ecols = [c for c, _ in ENTITIES["experiment"]["columns"]] + ["id"]
    for eid in eids:
        e = p.get("experiment", eid)
        e["state"] = S.experiment_state(p, eid)
        e["children"] = [r["id"] for r in p.q("SELECT id FROM experiments WHERE parent_id=? ORDER BY id", (eid,))]
        e["runs"] = [_run_record(p, r) for r in sorted(list_runs(p, experiment=eid), key=lambda r: r["id"])]
        e["artifacts"] = _artifacts(p, "SELECT * FROM artifacts WHERE experiment_id=? AND run_id IS NULL ORDER BY id", (eid,))
        e["syntheses"] = S.list_syntheses(p, eid)
        e["findings"] = sorted({l["src_id"] for l in p.q(
            "SELECT src_id FROM links WHERE src_type='finding' AND (dst_id=? "
            "OR dst_id IN (SELECT id FROM runs WHERE experiment_id=?) "
            "OR dst_id IN (SELECT id FROM artifacts WHERE experiment_id=?))", (eid, eid, eid))})
        e["decisions"] = sorted({l["src_id"] for l in p.links_to(eid) if l["src_type"] == "decision"})
        e["tasks"] = [r["id"] for r in p.q("SELECT id FROM tasks WHERE related_experiment_id=? ORDER BY id", (eid,))]
        e["_unknown"] = _unknown_fields(_raw_columns(p, "experiment", eid), ecols)
        snap["experiments"].append(e)

    # ---- findings in scope (every finding when unscoped; those touching scope otherwise)
    if stype is None:
        fids = [r["id"] for r in p.q("SELECT id FROM findings ORDER BY id")]
    else:
        touch = set(eids) | set(qids) | {r["id"] for e in eids for r in p.q("SELECT id FROM runs WHERE experiment_id=?", (e,))} \
            | {r["id"] for e in eids for r in p.q("SELECT id FROM artifacts WHERE experiment_id=?", (e,))}
        fids = sorted({l["src_id"] for t in sorted(touch) for l in p.links_to(t) if l["src_type"] == "finding"})
    snap["findings"] = []
    fcols = [c for c, _ in ENTITIES["finding"]["columns"]] + ["id"]
    for fid in fids:
        f = p.get("finding", fid)
        f["reviews"] = V.list_records(p, fid, "review")
        f["supersedes"] = [r["id"] for r in p.q("SELECT id FROM findings WHERE superseded_by=? ORDER BY id", (fid,))]
        f["referenced_by"] = sorted({l["src_id"] for l in p.links_to(fid)})
        f["bindings"] = list_bindings(p, fid, include_superseded=True)
        f["verification"] = verify_finding(p, fid) if f["bindings"] else None
        f["_unknown"] = _unknown_fields(_raw_columns(p, "finding", fid), fcols)
        snap["findings"].append(f)

    # ---- decisions (all statuses, incl. superseded)
    if stype is None:
        dids = [r["id"] for r in p.q("SELECT id FROM decisions ORDER BY id")]
    else:
        dids = sorted({l["src_id"] for t in sorted(set(eids) | set(fids)) for l in p.links_to(t)
                       if l["src_type"] == "decision"})
    snap["decisions"] = []
    dcols = [c for c, _ in ENTITIES["decision"]["columns"]] + ["id"]
    for did in dids:
        d = p.get("decision", did)
        d["supersedes"] = [r["id"] for r in p.q("SELECT id FROM decisions WHERE superseded_by=? ORDER BY id", (did,))]
        d["_unknown"] = _unknown_fields(_raw_columns(p, "decision", did), dcols)
        snap["decisions"].append(d)

    # ---- checkpoints, notes, sources, events (project-level: whole project only)
    snap["checkpoints"] = p.list("checkpoint", order="id") if stype is None else []
    snap["notes"] = []
    if stype is None:
        for n in sorted(S.list_notes(p), key=lambda x: x["id"]):
            path = p.abspath(n["path"]) or ""
            try:
                with open(path, encoding="utf-8") as fh:
                    n = {**n, "text": fh.read(), "readable": True}
            except OSError as exc:
                n = {**n, "text": None, "readable": False, "read_error": str(exc)}
            n.pop("modified", None)  # filesystem mtime: volatile, excluded from the deterministic export
            snap["notes"].append(n)
        from .evidence import source_list
        snap["sources"] = sorted(source_list(p), key=lambda s: s["id"])
        snap["events"] = _events(p)
    else:
        snap["sources"] = []
        snap["events"] = _events(p, sorted(set(eids) | set(qids) | set(fids) | set(dids) | set(plan_ids)
                                           | {r["id"] for e in eids for r in p.q(
                                               "SELECT id FROM runs WHERE experiment_id=?", (e,))}))

    # ---- project-level (not run-level) metrics, and artifacts attached to neither
    snap["project_metrics"] = [
        {"name": m["name"], "value": m["value"], "text": m["value_text"], "step": m["step"], "unit": m["unit"],
         "timestamp": m["timestamp"], "author_type": m["author_type"], "experiment_id": m["experiment_id"],
         "_n": n}
        for n, m in enumerate(p.q("SELECT * FROM metrics WHERE run_id IS NULL" +
                     ("" if stype is None else " AND experiment_id IN (%s)" % (",".join("?" * len(eids)) or "NULL")) +
                     " ORDER BY COALESCE(experiment_id,''), name, COALESCE(step,-1), id",
                     () if stype is None else tuple(eids)), 1)]
    snap["loose_artifacts"] = _artifacts(
        p, "SELECT * FROM artifacts WHERE run_id IS NULL AND experiment_id IS NULL ORDER BY id", ()) if stype is None else []

    snap["integrity"] = _integrity(p, snap)
    snap["counts"] = _counts(snap)
    return snap


def _events(p: Project, entity_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Events in recorded append order.

    `seq` is the position in that order, not the SQLite rowid: a rebuild re-reads events.jsonl and may append
    its own system events, which would shift rowids but not the order of what came before.
    """
    if entity_ids is None:
        rows = p.q("SELECT * FROM events ORDER BY id")
    elif not entity_ids:
        return []
    else:
        rows = p.q("SELECT * FROM events WHERE entity_id IN (%s) ORDER BY id" % ",".join("?" * len(entity_ids)),
                   tuple(entity_ids))
    return [{"seq": n, "ts": r["ts"], "entity_type": r["entity_type"], "entity_id": r["entity_id"],
             "action": r["action"], "summary": r["summary"], "author_type": r["author_type"],
             "author_name": r["author_name"]} for n, r in enumerate(rows, 1)]


def _integrity(p: Project, snap: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Source-data problems: missing artifact files, dangling references, incomplete runs, never-recorded fields.

    These are *source* incompleteness, not exporter failures: they are reported, never inferred away.
    """
    missing_art, dangling, incomplete, unsynth = [], [], [], []
    known = set()
    for spec in ENTITIES.values():
        known |= {r["id"] for r in p.q(f"SELECT id FROM {spec['table']}")}
    known |= {r["id"] for r in p.q("SELECT id FROM runs")} | {r["id"] for r in p.q("SELECT id FROM artifacts")}
    for e in snap["experiments"]:
        for a in e["artifacts"]:
            if not a["exists"]:
                missing_art.append({"artifact": a["id"], "path": a["path"], "owner": e["id"]})
        for r in e["runs"]:
            for a in r["artifacts"]:
                if not a["exists"]:
                    missing_art.append({"artifact": a["id"], "path": a["path"], "owner": r["id"]})
            if r["status"] in ("queued", "running"):
                incomplete.append({"run": r["id"], "status": r["status"], "experiment": e["id"],
                                   "slurm_job_id": r.get("slurm_job_id")})
            elif r["status"] == "unknown":
                incomplete.append({"run": r["id"], "status": "unknown", "experiment": e["id"],
                                   "slurm_job_id": r.get("slurm_job_id")})
        if e["state"]["unsynthesized"]:
            unsynth.append({"experiment": e["id"], "runs": e["state"]["unsynthesized_ids"]})
    for a in snap.get("loose_artifacts", []):
        if not a["exists"]:
            missing_art.append({"artifact": a["id"], "path": a["path"], "owner": None})
    for f in snap["findings"]:
        for rel, ids in sorted((f.get("links") or {}).items()):
            for i in ids:
                if i not in known and not str(i).startswith("note:"):
                    dangling.append({"from": f["id"], "relation": rel, "to": i})
    for n in snap["notes"]:
        for i in n.get("links") or []:
            if i not in known and not str(i).startswith("note:"):
                dangling.append({"from": n["id"], "relation": "links", "to": i})
        if not n.get("readable"):
            missing_art.append({"artifact": n["id"], "path": n["path"], "owner": None})
    never_recorded = []
    for e in snap["experiments"]:
        blank = [c for c, _heading in ENTITIES["experiment"]["sections"] if not (e.get(c) or "").strip()]
        if blank:
            never_recorded.append({"entity": e["id"], "fields": blank})
    return {"missing_artifacts": sorted(missing_art, key=lambda x: (str(x["artifact"]), str(x["path"]))),
            "dangling_references": sorted(dangling, key=lambda x: (x["from"], x["relation"], x["to"])),
            "incomplete_runs": sorted(incomplete, key=lambda x: x["run"]),
            "unsynthesized_experiments": sorted(unsynth, key=lambda x: x["experiment"]),
            "never_recorded_fields": sorted(never_recorded, key=lambda x: x["entity"])}


def _counts(snap: Dict[str, Any]) -> Dict[str, int]:
    runs = [r for e in snap["experiments"] for r in e["runs"]]
    return {
        "question": len(snap["questions"]), "plan": len(snap["plans"]),
        "task": sum(len(pl["tasks"]) for pl in snap["plans"]),
        "experiment": len(snap["experiments"]), "run": len(runs),
        "metric": sum(len(r["metrics"]) for r in runs) + len(snap["project_metrics"]),
        "artifact": sum(len(r["artifacts"]) for r in runs) + sum(len(e["artifacts"]) for e in snap["experiments"])
        + len(snap["loose_artifacts"]),
        "synthesis": sum(len(e["syntheses"]) for e in snap["experiments"]),
        "finding": len(snap["findings"]),
        "review": sum(len(f["reviews"]) for f in snap["findings"])
        + sum(len(t["check_records"]) for pl in snap["plans"] for t in pl["tasks"]),
        "decision": len(snap["decisions"]), "checkpoint": len(snap["checkpoints"]),
        "note": len(snap["notes"]), "source": len(snap["sources"]), "event": len(snap["events"]),
    }


def record_ids(snap: Dict[str, Any]) -> Dict[str, List[str]]:
    """The authoritative id list per category — what the renderer must emit exactly once each."""
    runs = [r for e in snap["experiments"] for r in e["runs"]]
    ids: Dict[str, List[str]] = {
        "question": [q["id"] for q in snap["questions"]],
        "plan": [pl["id"] for pl in snap["plans"]],
        "task": [t["id"] for pl in snap["plans"] for t in pl["tasks"]],
        "experiment": [e["id"] for e in snap["experiments"]],
        "run": [r["id"] for r in runs],
        "metric": [f"{r['id']}#{m['_n']}" for r in runs for m in r["metrics"]]
        + [f"project#{m['_n']}" for m in snap["project_metrics"]],
        "artifact": [a["id"] for r in runs for a in r["artifacts"]]
        + [a["id"] for e in snap["experiments"] for a in e["artifacts"]]
        + [a["id"] for a in snap["loose_artifacts"]],
        "synthesis": [s["id"] for e in snap["experiments"] for s in e["syntheses"]],
        "finding": [f["id"] for f in snap["findings"]],
        "review": [r["id"] for f in snap["findings"] for r in f["reviews"]]
        + [r["id"] for pl in snap["plans"] for t in pl["tasks"] for r in t["check_records"]],
        "decision": [d["id"] for d in snap["decisions"]],
        "checkpoint": [c["id"] for c in snap["checkpoints"]],
        "note": [n["id"] for n in snap["notes"]],
        "source": [s["id"] for s in snap["sources"]],
        "event": [f"E{e['seq']}" for e in snap["events"]],
    }
    return ids


# =============================================================================== rendering

class _Doc:
    def __init__(self) -> None:
        self.L: List[str] = []

    def line(self, s: str = "") -> None:
        self.L.append(s)

    def h(self, level: int, text: str) -> None:
        self.L += ["", "#" * level + " " + text, ""]

    def para(self, text: str) -> None:
        self.L += [text, ""]

    def mark(self, kind: str, id_: str) -> None:
        self.L.append(f"<!-- {MARKER} {kind} {id_} -->")

    def fields(self, pairs: List[Tuple[str, Any]], *, raw: Tuple[str, ...] = ()) -> None:
        """A field list. Values that are blocks (multi-line) are rendered under their own sub-heading."""
        for label, v in pairs:
            if label in raw:
                continue
            self.L.append(f"- **{label}:** {_val(v)}")
        self.L.append("")

    def blocks(self, pairs: List[Tuple[str, Any]]) -> None:
        for label, v in pairs:
            text = _block(v)
            self.L += [f"**{label}:**", ""]
            self.L += ([text, ""] if text.strip() else ["_not recorded_", ""])

    def table(self, header: List[str], rows: List[List[Any]], empty: str = "_none_") -> None:
        if not rows:
            self.L += [empty, ""]
            return
        self.L.append("| " + " | ".join(header) + " |")
        self.L.append("|" + "---|" * len(header))
        for row in rows:
            self.L.append("| " + " | ".join(_cell(c) for c in row) + " |")
        self.L.append("")

    def code(self, text: str, lang: str = "") -> None:
        self.L += ["```" + lang, _block(text), "```", ""]

    def text(self) -> str:
        out: List[str] = []
        for l in self.L:  # collapse runs of blank lines so spacing is canonical
            if l == "" and out and out[-1] == "":
                continue
            out.append(l)
        return "\n".join(out).strip("\n") + "\n"


_RUN_FIELDS = ["id", "experiment_id", "label", "status", "exit_code", "backend", "command", "working_dir",
               "parameters", "env", "hostname", "pid", "slurm_job_id", "slurm", "run_dir", "stdout_path",
               "stderr_path", "git_branch", "git_commit", "git_dirty", "reviewed", "override_reason",
               "created_at", "started_at", "ended_at", "updated_at", "duration_s",
               "author_type", "author_name", "author_model", "notes"]


def _render_run(d: _Doc, r: Dict[str, Any], level: int) -> None:
    d.h(level, f"{r['id']} — {r.get('label') or r['status']}")
    d.mark("run", r["id"])
    d.fields([(k.replace("_", " "), r.get(k)) for k in _RUN_FIELDS])
    unknown = _unknown_fields(r, _RUN_FIELDS + ["metrics", "artifacts", "run_dir_files", "run_dir_exists",
                                                "metrics_offset", "artifacts_offset"])
    if unknown:
        d.para("Additional recorded fields:")
        d.code(_json(unknown), "json")
    if r.get("notes"):
        d.blocks([("Run notes", r["notes"])])
    d.para(f"Run directory `{r.get('run_dir')}`: "
           + ("present — " + (", ".join(f"{f['name']} ({_num(f['size'])} B)" for f in r["run_dir_files"]) or "empty")
              if r["run_dir_exists"] else "**missing on this machine**"))
    if r["metrics"]:
        d.para(f"Metric entries ({len(r['metrics'])}, complete history):")
        d.table(["#", "metric", "value", "text", "step", "unit", "timestamp", "author"],
                [[f"{r['id']}#{m['_n']}", m["name"], m["value"], m["text"], m["step"], m["unit"],
                  m["timestamp"], m["author_type"]] for m in r["metrics"]])
        for m in r["metrics"]:
            d.mark("metric", f"{r['id']}#{m['_n']}")
        d.line("")
    else:
        d.para("Metric entries: _none recorded_")
    if r["artifacts"]:
        d.para(f"Artifacts ({len(r['artifacts'])}):")
        _artifact_table(d, r["artifacts"])
    else:
        d.para("Artifacts: _none registered_")


def _artifact_table(d: _Doc, arts: List[Dict[str, Any]]) -> None:
    d.table(["id", "name", "type", "path", "external", "size", "hash", "mtime", "available", "description",
             "tags", "author", "created"],
            [[a["id"], a["name"], a["type"], a["path"], a["external"], a["size"], a["hash"], a["mtime"],
              "yes" if a["exists"] else "NO (missing)", a["description"], a["tags"],
              a["author_name"] or a["author_type"], a["created_at"]] for a in arts])
    for a in arts:
        d.mark("artifact", a["id"])
    d.line("")


def render(snap: Dict[str, Any], *, stamp: bool = False) -> str:
    """Render the snapshot. Deterministic unless `stamp=True` (which adds a wall-clock generation time)."""
    d = _Doc()
    counts = snap["counts"]
    scope = snap["scope"]

    d.line("# Research Context Export")
    d.line("")
    d.para("Complete recorded research context, rendered directly from the canonical Research OS records. "
           "Nothing here is summarized, inferred or model-generated; fields that were never recorded say so.")

    # ---- 1. metadata
    d.h(2, SECTIONS[0])
    meta = [("export schema", snap["export_schema"]), ("research version", snap["tool_version"]),
            ("source schema version", snap["source_schema_version"]),
            ("scope", scope), ("scope type", snap["scope_type"]),
            ("project", snap["project"]["name"]),
            ("deterministic", "no (--stamp: contains a generation timestamp)" if stamp else "yes")]
    if stamp:
        from .util import now_iso
        meta.append(("generated at", now_iso()))
    d.fields(meta)
    d.para("Record counts in this export:")
    d.table(["category", "records"], [[c, counts[c]] for c in CATEGORIES])

    # ---- 2. project definition
    d.h(2, SECTIONS[1])
    pr = snap["project"]
    cfg = pr["config"]
    d.fields([("name", cfg["project"].get("name")), ("goal", cfg["project"].get("goal")),
              ("description", cfg["project"].get("description")), ("status", cfg["project"].get("status")),
              ("created at", cfg["project"].get("created_at")), ("git repo", cfg["project"].get("git_repo")),
              ("default backend", cfg["project"].get("default_backend")),
              ("git branch", pr["git"].get("branch")), ("git commit", pr["git"].get("commit")),
              ("git working tree dirty", pr["git"].get("dirty"))])
    d.para("Full configuration as recorded (`.research/config.yaml`, merged with defaults):")
    d.code(_json(cfg), "json")

    # ---- 3. current state
    d.h(2, SECTIONS[2])
    d.para("Authoritative entity state (not history — see section 12 for the event record).")
    d.table(["entity", "id", "title", "status", "author"],
            [["question", q["id"], q["title"], q["status"], q.get("author_name") or q.get("author_type")]
             for q in snap["questions"]]
            + [["experiment", e["id"], e["title"], e["status"], e.get("author_name") or e.get("author_type")]
               for e in snap["experiments"]]
            + [["plan", pl["id"], pl["title"], pl["status"], pl.get("author_name") or pl.get("author_type")]
               for pl in snap["plans"]]
            + [["finding", f["id"], f["title"], f["status"], f.get("author_name") or f.get("author_type")]
               for f in snap["findings"]]
            + [["decision", x["id"], x["title"], x["status"], x.get("author_name") or x.get("author_type")]
               for x in snap["decisions"]])
    if snap["checkpoints"]:
        last = snap["checkpoints"][-1]
        d.para(f"Latest checkpoint: {last['id']} ({last.get('created_at')}) — see section 9.")

    # ---- 4. questions
    d.h(2, SECTIONS[3])
    if not snap["questions"]:
        d.para("_No questions recorded._")
    for q in snap["questions"]:
        d.h(3, f"{q['id']} — {q['title']}")
        d.mark("question", q["id"])
        d.fields([("status", q["status"]), ("parent", q.get("parent_id")), ("children", q["children"]),
                  ("tags", q.get("tags")), ("experiments", q["experiments"]), ("findings", q["findings"]),
                  ("author", q.get("author_name") or q.get("author_type")), ("author model", q.get("author_model")),
                  ("created at", q.get("created_at")), ("updated at", q.get("updated_at"))])
        d.blocks([("Description", q.get("description"))])
        if q["_unknown"]:
            d.para("Additional recorded fields:")
            d.code(_json(q["_unknown"]), "json")

    # ---- 5. plans and tasks
    d.h(2, SECTIONS[4])
    if not snap["plans"]:
        d.para("_No plans recorded._")
    for pl in snap["plans"]:
        d.h(3, f"{pl['id']} — {pl['title']}")
        d.mark("plan", pl["id"])
        d.fields([("status", pl["status"]), ("root question", pl.get("root_question_id")),
                  ("skills", pl.get("skills")), ("tasks", pl.get("task_count")),
                  ("tasks done", pl.get("tasks_done")),
                  ("author", pl.get("author_name") or pl.get("author_type")),
                  ("created at", pl.get("created_at")), ("updated at", pl.get("updated_at")),
                  ("completed at", pl.get("completed_at"))])
        d.blocks([("Objective", pl.get("objective")), ("Success criteria", pl.get("success_criteria")),
                  ("Context", pl.get("context"))])
        for t in pl["tasks"]:
            d.h(4, f"{t['id']} — {t['title']}")
            d.mark("task", t["id"])
            d.fields([("plan", t.get("plan_id")), ("status", t["status"]), ("type", t.get("task_type")),
                      ("assigned role", t.get("assigned_role")), ("depends on", t.get("depends_on")),
                      ("ready", t.get("is_ready")), ("related question", t.get("related_question_id")),
                      ("related experiment", t.get("related_experiment_id")),
                      ("artifacts", t.get("artifacts")), ("skills", t.get("skills")),
                      ("checks", t.get("checks")), ("checks described", t["checks_described"]),
                      ("claimed by", t.get("claimed_by")), ("claimed at", t.get("claimed_at")),
                      ("completed by", t.get("completed_by")), ("started at", t.get("started_at")),
                      ("completed at", t.get("completed_at")),
                      ("author", t.get("author_name") or t.get("author_type")),
                      ("created at", t.get("created_at")), ("updated at", t.get("updated_at"))])
            d.blocks([("Goal", t.get("goal")), ("Inputs", t.get("inputs")),
                      ("Expected outputs", t.get("expected_outputs")),
                      ("Acceptance criteria", t.get("acceptance_criteria")),
                      ("Verification", t.get("verification")), ("Result", t.get("result")),
                      ("Blockers", t.get("blockers")), ("Notes", t.get("notes"))])
            if t["check_records"]:
                d.para("Verification check records:")
                d.table(["id", "verdict", "summary", "author", "created"],
                        [[r["id"], r["verdict"], r["summary"], r.get("author_name") or r.get("author_type"),
                          r.get("created_at")] for r in t["check_records"]])
                for r in t["check_records"]:
                    d.mark("review", r["id"])
                d.line("")
                for r in t["check_records"]:
                    if r.get("details") is not None:
                        d.para(f"Details of {r['id']}:")
                        d.code(_json(r["details"]), "json")
            if t["progress_notes"]:
                d.para("Progress notes (claim/hand-off trail, as recorded):")
                d.table(["ts", "author", "note"],
                        [[n.get("ts"), n.get("author"), n.get("text")] for n in t["progress_notes"]])

    # ---- 6. experiments
    d.h(2, SECTIONS[5])
    if not snap["experiments"]:
        d.para("_No experiments recorded._")
    for e in snap["experiments"]:
        d.h(3, f"{e['id']} — {e['title']}")
        d.mark("experiment", e["id"])
        d.h(4, "Experimental Design")
        d.fields([("status", e["status"]), ("question", e.get("question_id")),
                  ("derived from", e.get("parent_id")), ("variants", e["children"]),
                  ("baseline", e.get("is_baseline")), ("parameters", e.get("parameters")),
                  ("parameter delta vs parent", e.get("param_delta")),
                  ("metrics requested", e.get("metrics_requested")),
                  ("expected artifacts", e.get("expected_artifacts")),
                  ("planned runs", e.get("planned_runs")), ("tags", e.get("tags"))])
        d.blocks([("Hypothesis", e.get("hypothesis")), ("Motivation", e.get("motivation")),
                  ("Method", e.get("method")), ("Expected outcome", e.get("expected_outcome")),
                  ("Success criteria", e.get("success_criteria")),
                  ("Stop conditions", e.get("stop_conditions")),
                  ("Known limitations", e.get("limitations")), ("Notes", e.get("notes"))])
        d.h(4, "Implementation and Provenance")
        d.para("Research OS records the code state at registration and per run. It does not record a prose "
               "description of what was implemented unless a task result or synthesis says so.")
        d.fields([("git branch at registration", e.get("git_branch")),
                  ("git commit at registration", e.get("git_commit")),
                  ("working tree dirty at registration", e.get("git_dirty")),
                  ("author", e.get("author_name") or e.get("author_type")),
                  ("author model", e.get("author_model")),
                  ("created at", e.get("created_at")), ("updated at", e.get("updated_at")),
                  ("started at", e.get("started_at")), ("completed at", e.get("completed_at")),
                  ("tasks referencing this experiment", e["tasks"])])
        commits = sorted({r.get("git_commit") for r in e["runs"] if r.get("git_commit")})
        d.fields([("distinct run commits", commits),
                  ("runs with a dirty working tree",
                   [r["id"] for r in e["runs"] if r.get("git_dirty")] or None),
                  ("recorded diffs",
                   [f"{r['id']}: {r['run_dir']}/git.diff" for r in e["runs"]
                    if any(f["name"] == "git.diff" for f in r["run_dir_files"])] or None)])
        if e["_unknown"]:
            d.para("Additional recorded experiment fields:")
            d.code(_json(e["_unknown"]), "json")
        d.h(4, "Runs")
        st = e["state"]
        d.fields([("total runs", st["runs"]), ("completed", st["completed"]), ("failed", st["failed"]),
                  ("active (queued/running)", st["active"]), ("unsynthesized", st["unsynthesized"]),
                  ("unsynthesized run ids", st["unsynthesized_ids"]),
                  ("failed runs awaiting review", st["unreviewed_failed"])])
        d.para(f"Run inventory ({len(e['runs'])} runs, all statuses, complete):")
        d.table(["run", "status", "exit", "backend", "slurm job", "host", "started", "ended", "duration s",
                 "parameters", "label"],
                [[r["id"], r["status"], r.get("exit_code"), r.get("backend"), r.get("slurm_job_id"),
                  r.get("hostname"), r.get("started_at"), r.get("ended_at"), r.get("duration_s"),
                  r.get("parameters"), r.get("label")] for r in e["runs"]])
        for r in e["runs"]:
            _render_run(d, r, 5)
        d.h(4, "Metrics")
        final = {}
        for r in e["runs"]:
            for m in r["metrics"]:
                final[(r["id"], m["name"])] = m
        if final:
            d.para("Final value per run and metric (convenience view; the full histories are under each run):")
            d.table(["run", "metric", "value", "text", "step", "unit"],
                    [[k[0], k[1], m["value"], m["text"], m["step"], m["unit"]]
                     for k, m in sorted(final.items())])
        else:
            d.para("_No metrics recorded for this experiment's runs._")
        exp_metrics = [m for m in snap["project_metrics"] if m.get("experiment_id") == e["id"]]
        if exp_metrics:
            d.para("Metrics recorded on the experiment itself (not on a run):")
            d.table(["#", "metric", "value", "text", "step", "unit", "timestamp", "author"],
                    [[f"project#{m['_n']}", m["name"], m["value"], m["text"], m["step"], m["unit"],
                      m["timestamp"], m["author_type"]] for m in exp_metrics])
            for m in exp_metrics:
                d.mark("metric", f"project#{m['_n']}")
            d.line("")
        d.h(4, "Artifacts")
        if e["artifacts"]:
            d.para("Registered on the experiment (run artifacts are listed under their run):")
            _artifact_table(d, e["artifacts"])
        else:
            d.para("_No artifacts registered on the experiment itself._")
        d.h(4, "Syntheses")
        if not e["syntheses"]:
            d.para("_Never synthesized._")
        for s in e["syntheses"]:
            d.h(5, f"{s['id']}")
            d.mark("synthesis", s["id"])
            d.fields([("created at", s.get("created_at")),
                      ("author", s.get("author_name") or s.get("author_type")),
                      ("author model", s.get("author_model")), ("runs covered", s.get("runs_covered"))])
            d.blocks([("What happened", s.get("what_happened")), ("What worked", s.get("what_worked")),
                      ("What failed", s.get("what_failed")), ("Interpretation", s.get("interpretation")),
                      ("Limitations", s.get("limitations")), ("Unresolved", s.get("unresolved")),
                      ("Recommended next experiment", s.get("next_experiment"))])
        d.h(4, "Linked Findings and Decisions")
        d.fields([("findings citing this experiment or its records", e["findings"]),
                  ("decisions referencing this experiment", e["decisions"])])

    # ---- 7. findings and reviews
    d.h(2, SECTIONS[6])
    d.para("Status is as recorded. A preliminary or agent-authored finding is **not** an established fact; "
           "`supported` requires an independent review, which is listed per finding below.")
    if not snap["findings"]:
        d.para("_No findings recorded._")
    for f in snap["findings"]:
        d.h(3, f"{f['id']} — {f['title']}")
        d.mark("finding", f["id"])
        L = f.get("links") or {}
        d.fields([("kind", f.get("kind")), ("status", f["status"]), ("confidence", f.get("confidence")),
                  ("author", f.get("author_name") or f.get("author_type")),
                  ("author model", f.get("author_model")),
                  ("created at", f.get("created_at")), ("updated at", f.get("updated_at")),
                  ("supporting evidence", L.get("supports")),
                  ("contradicting evidence", L.get("contradicts")),
                  ("related", L.get("related")), ("addresses questions", L.get("questions")),
                  ("superseded by", f.get("superseded_by")), ("supersedes", f["supersedes"]),
                  ("referenced by", f["referenced_by"]), ("tags", f.get("tags"))])
        d.blocks([("Statement", f.get("statement")), ("Limitations", f.get("limitations")),
                  ("Contradicting evidence (text)", f.get("contradicting_evidence"))])
        if f["reviews"]:
            d.para("Independent review records:")
            d.table(["id", "verdict", "summary", "reviewer", "reviewer type", "created"],
                    [[r["id"], r["verdict"], r["summary"], r.get("author_name"), r.get("author_type"),
                      r.get("created_at")] for r in f["reviews"]])
            for r in f["reviews"]:
                d.mark("review", r["id"])
            d.line("")
            for r in f["reviews"]:
                if r.get("details") is not None:
                    d.para(f"Details of {r['id']}:")
                    d.code(_json(r["details"]), "json")
        else:
            d.para("Independent review records: _none_")
        if f["bindings"]:
            d.para("Numerical evidence bindings (each number in the finding bound to its record):")
            vmap = {b["id"]: b for b in (f["verification"] or {}).get("bindings", [])}
            rows = []
            for b in f["bindings"]:
                v = vmap.get(b["id"], {})
                rows.append([b["id"], b.get("kind"), b.get("claimed"), b.get("unit"), b.get("partition"),
                             _json(b.get("source")), (b.get("artifact") or {}).get("id"),
                             v.get("status", "not re-verified"),
                             "; ".join(v.get("issues", []) + v.get("warnings", [])) or None,
                             b.get("superseded_by"), b.get("note")])
            d.table(["binding", "kind", "claimed", "unit", "partition", "source", "artifact",
                     "verification", "notes", "superseded by", "note"], rows)
            d.para("_Verification re-checks the number against the recorded evidence. It says nothing about "
                   "whether the interpretation is correct._")
        if f["_unknown"]:
            d.para("Additional recorded fields:")
            d.code(_json(f["_unknown"]), "json")

    # ---- 8. decisions
    d.h(2, SECTIONS[7])
    if not snap["decisions"]:
        d.para("_No decisions recorded._")
    for x in snap["decisions"]:
        d.h(3, f"{x['id']} — {x['title']}")
        d.mark("decision", x["id"])
        L = x.get("links") or {}
        d.fields([("status", x["status"]), ("date", x.get("date")),
                  ("supporting findings", L.get("supporting_findings")),
                  ("related experiments", L.get("experiments")),
                  ("superseded by", x.get("superseded_by")), ("supersedes", x["supersedes"]),
                  ("author", x.get("author_name") or x.get("author_type")),
                  ("created at", x.get("created_at")), ("updated at", x.get("updated_at")),
                  ("tags", x.get("tags"))])
        d.blocks([("Decision", x.get("statement")), ("Reason", x.get("reason"))])
        if x["_unknown"]:
            d.para("Additional recorded fields:")
            d.code(_json(x["_unknown"]), "json")

    # ---- 9. checkpoints
    d.h(2, SECTIONS[8])
    if not snap["checkpoints"]:
        d.para("_No checkpoints recorded._")
    for c in snap["checkpoints"]:
        d.h(3, f"{c['id']} — {c['title']}")
        d.mark("checkpoint", c["id"])
        d.fields([("created at", c.get("created_at")), ("updated at", c.get("updated_at")),
                  ("author", c.get("author_name") or c.get("author_type")),
                  ("baseline experiment", c.get("baseline_experiment_id")),
                  ("important findings", c.get("finding_ids")), ("known failures", c.get("failure_ids")),
                  ("open questions", c.get("question_ids")), ("active experiments", c.get("experiment_ids")),
                  ("git branch", c.get("git_branch")), ("git commit", c.get("git_commit")),
                  ("git dirty", c.get("git_dirty"))])
        d.blocks([("Current goal", c.get("goal")), ("Current understanding", c.get("understanding")),
                  ("Baseline", c.get("baseline")), ("Current problems / blockers", c.get("current_problem")),
                  ("Next step", c.get("next_experiment")), ("Notes", c.get("notes"))])

    # ---- 10. notes
    d.h(2, SECTIONS[9])
    d.para("Human-authored notes under `.research/notes/`, verbatim.")
    if not snap["notes"]:
        d.para("_No notes recorded._")
    for n in snap["notes"]:
        d.h(3, f"{n['id']} — {n['title']}")
        d.mark("note", n["id"])
        d.fields([("file", n.get("path")), ("pinned", n.get("pinned")), ("links", n.get("links")),
                  ("created", n.get("created_at"))])
        if n.get("readable"):
            d.code(n["text"] or "")
        else:
            d.para(f"**File could not be read:** {n.get('read_error')}")
    if snap["loose_artifacts"]:
        # Artifacts registered against neither a run nor an experiment. They would otherwise have no
        # section to appear in, since every other artifact is listed under its owner.
        d.h(3, "Artifacts not attached to a run or experiment")
        d.para("Registered standalone, so they belong to the project rather than to one experiment.")
        _artifact_table(d, snap["loose_artifacts"])
    if snap["sources"]:
        d.h(3, "Literature sources")
        d.table(["id", "title", "doi", "arxiv", "url", "identity", "examined", "assessments"],
                [[s["id"], s.get("title"), s.get("doi"), s.get("arxiv"), s.get("url"), s.get("identity"),
                  _json(s.get("examined")) if s.get("examined") else None,
                  len(s.get("assessments") or [])] for s in snap["sources"]])
        for s in snap["sources"]:
            d.mark("source", s["id"])
        d.line("")
        for s in snap["sources"]:
            for a in s.get("assessments") or []:
                d.para(f"Assessment of {s['id']}: " + _json(a))

    # ---- 11. open work
    d.h(2, SECTIONS[10])
    integ = snap["integrity"]
    d.para("Open questions:")
    d.table(["question", "title", "status"],
            [[q["id"], q["title"], q["status"]] for q in snap["questions"]
             if q["status"] in ("open", "investigating", "blocked")])
    d.para("Experiments with runs that were never synthesized:")
    d.table(["experiment", "unsynthesized runs"],
            [[u["experiment"], u["runs"]] for u in integ["unsynthesized_experiments"]])
    d.para("Runs that are not in a terminal state (queued, running, or ended without a recorded status):")
    d.table(["run", "status", "experiment", "slurm job"],
            [[u["run"], u["status"], u["experiment"], u.get("slurm_job_id")] for u in integ["incomplete_runs"]])
    d.para("Tasks not done:")
    d.table(["task", "plan", "status", "blockers", "title"],
            [[t["id"], t.get("plan_id"), t["status"], t.get("blockers"), t["title"]]
             for pl in snap["plans"] for t in pl["tasks"] if t["status"] != "done"])
    d.para("Findings awaiting an independent review:")
    d.table(["finding", "status", "author", "title"],
            [[f["id"], f["status"], f.get("author_name") or f.get("author_type"), f["title"]]
             for f in snap["findings"] if f["status"] == "preliminary" and not f["reviews"]])
    nxt = [(e["id"], s["next_experiment"]) for e in snap["experiments"] for s in e["syntheses"]
           if (s.get("next_experiment") or "").strip()]
    d.para("Next steps as recorded in syntheses (the authors' words, not a recommendation from this export):")
    d.table(["experiment", "recorded next step"], [[a, b] for a, b in nxt])

    # ---- 12. events
    d.h(2, SECTIONS[11])
    d.para("Complete recorded event history in append order. Events are history; section 3 holds the "
           "authoritative current state.")
    d.table(["seq", "timestamp", "entity type", "entity", "action", "author type", "author", "summary"],
            [[f"E{ev['seq']}", ev["ts"], ev["entity_type"], ev["entity_id"], ev["action"],
              ev["author_type"], ev["author_name"], ev["summary"]] for ev in snap["events"]])
    for ev in snap["events"]:
        d.mark("event", f"E{ev['seq']}")
    d.line("")

    # ---- 13. data integrity
    d.h(2, SECTIONS[12])
    d.para("What Research OS recorded but cannot show, and what was never recorded. These are gaps in the "
           "source data, not omissions by this export; none of them is filled in by inference.")
    d.para("Registered artifacts whose file is not present on this machine:")
    d.table(["artifact", "path", "owner"],
            [[m["artifact"], m["path"], m["owner"]] for m in integ["missing_artifacts"]])
    d.para("References pointing at ids that do not exist:")
    d.table(["from", "relation", "to"],
            [[x["from"], x["relation"], x["to"]] for x in integ["dangling_references"]])
    d.para("Experiment design fields that were never filled in:")
    d.table(["experiment", "fields never recorded"],
            [[x["entity"], x["fields"]] for x in integ["never_recorded_fields"]])
    d.para("Inherently unavailable in this export: the contents of registered artifacts and logs (referenced by "
           "path, never embedded), external datasets, and any reasoning that was not written into a record.")

    # ---- 14. coverage manifest
    d.h(2, SECTIONS[13])
    d.para("Authoritative record count per category, from the same snapshot that produced this document. "
           "`research report --full --check` re-derives these from the markers in the rendered Markdown.")
    d.code("\n".join(f"{(c + ':').ljust(14)} {counts[c]}" for c in CATEGORIES))
    return d.text()


# =============================================================================== coverage verification

def coverage(snap: Dict[str, Any], text: str) -> Dict[str, Any]:
    """Verify the rendered document against the snapshot, by parsing the document's own record markers.

    Independent of the renderer's bookkeeping: it reads the emitted Markdown, not the exporter's counters.
    Distinguishes exporter failure (missing/duplicate/unexpected records, malformed structure) from
    source-data incompleteness (missing files, dangling references, unsynthesized experiments).
    """
    found: Dict[str, List[str]] = {c: [] for c in CATEGORIES}
    unexpected_kinds: List[str] = []
    for kind, id_ in _MARKER_RE.findall(text):
        if kind in found:
            found[kind].append(id_)
        else:
            unexpected_kinds.append(f"{kind} {id_}")

    expected = record_ids(snap)
    cats, problems = {}, []
    for c in CATEGORIES:
        exp, got = expected[c], found[c]
        missing = [i for i in exp if i not in set(got)]
        dupes = sorted({i for i in got if got.count(i) > 1})
        extra = [i for i in got if i not in set(exp)]
        cats[c] = {"expected": len(exp), "emitted": len(set(got)), "markers": len(got),
                   "missing": missing, "duplicated": dupes, "unexpected": extra,
                   "ok": not (missing or dupes or extra)}
        for i in missing:
            problems.append({"category": c, "kind": "missing", "id": i,
                             "detail": f"{c} {i} is in the snapshot but was not emitted"})
        for i in dupes:
            problems.append({"category": c, "kind": "duplicated", "id": i,
                             "detail": f"{c} {i} was emitted more than once"})
        for i in extra:
            problems.append({"category": c, "kind": "unexpected", "id": i,
                             "detail": f"{c} {i} was emitted but is not in the snapshot"})
    for u in unexpected_kinds:
        problems.append({"category": "?", "kind": "unknown_marker", "id": u,
                         "detail": f"marker of unknown category: {u}"})

    # structural checks on the rendered document
    structure = []
    for title in SECTIONS:
        if f"\n## {title}\n" not in text:
            structure.append(f"required section missing or renamed: ## {title}")
    if not text.startswith("# Research Context Export\n"):
        structure.append("document does not start with the export heading")
    for bad in re.findall(r"^\|.*\|\s*$", text, re.M):
        if bad.count("|") < 2:
            structure.append(f"malformed table row: {bad[:60]}")
    for s in structure:
        problems.append({"category": "document", "kind": "malformed", "id": None, "detail": s})

    integ = snap["integrity"]
    source_issues = {k: len(v) for k, v in sorted(integ.items())}
    return {
        "ok": not problems,
        "export_schema": EXPORT_SCHEMA, "scope": snap["scope"],
        "source_schema_version": snap["source_schema_version"],
        "categories": cats,
        "exporter_problems": problems,
        "source_incompleteness": {**source_issues, "detail": integ},
        "bytes": len(text.encode("utf-8")), "lines": text.count("\n"),
    }


# =============================================================================== entry points

def default_path(p: Project, scope: Optional[str]) -> str:
    stem = str(normalize_id(scope) if scope else "project").replace("/", "-")
    return p.path("reports", f"{stem}-full.md")


def export(p: Project, scope: Optional[str] = None, *, stamp: bool = False) -> Tuple[Dict[str, Any], str]:
    """Snapshot + render. No writes, no events. Returns (snapshot, markdown)."""
    snap = snapshot(p, scope)
    return snap, render(snap, stamp=stamp)


def write(p: Project, scope: Optional[str] = None, out: Optional[str] = None, *, stamp: bool = False,
          check: bool = False) -> Dict[str, Any]:
    """Write the full export (default `.research/reports/<scope>-full.md`). Logs no event, mutates no records."""
    snap, text = export(p, scope, stamp=stamp)
    path = os.path.abspath(out) if out else default_path(p, scope)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, text)
    res: Dict[str, Any] = {"path": p.rel(path), "bytes": len(text.encode("utf-8")),
                           "counts": snap["counts"], "scope": snap["scope"]}
    if check:
        res["check"] = coverage(snap, text)
    return res
