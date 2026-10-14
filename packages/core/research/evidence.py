"""Deterministic numerical evidence: bindings, verification, artifact integrity, run provenance, literature sources.

A *binding* ties a number in a finding to its canonical source: a recorded metric (`kind: metric`) or a declared,
whitelisted calculation over recorded metrics (`kind: derived`; no eval). Bindings are append-only lines in
.research/evidence.jsonl (id `F-003/B1`); a correction is a new binding with `supersedes`, never an edit.
Verification re-reads the live records every time and returns one of:
    verified · mismatch · missing · stale · unverifiable
Nothing here is an LLM opinion. It checks numbers, not science: a finding with verified numbers still needs an
independent review of the interpretation. Findings without bindings (qualitative or legacy) are unaffected.

Also: artifact integrity (streaming hashes, append-only pins in .research/integrity.jsonl, registered hashes are never
rewritten), run provenance with explicit limitations, and lightweight literature sources (.research/sources.jsonl).
"""
from __future__ import annotations

from .durability import atomic

import json
import math
import os
import re
from typing import Any, Dict, List, Optional

from .schema import normalize_id
from .store import Project, append_jsonl
from .util import NotFound, PolicyBlocked, ResearchError, as_list, jload, now_iso, sha256_file

STATUSES = ["verified", "mismatch", "missing", "stale", "unverifiable"]
PARTITIONS = ["design", "validation", "test"]
MAX_REL_TOL = 0.05
SELECT = ["first", "last", "min", "max"]
_REL = ("relative_improvement", "relative_change")
OPS = {  # name → (arity check, fn, output-unit rule)
    "mean": (lambda n: n >= 1, lambda v: sum(v) / len(v)),
    "sum": (lambda n: n >= 1, sum),
    "min": (lambda n: n >= 1, min),
    "max": (lambda n: n >= 1, max),
    "std": (lambda n: n >= 2, lambda v: math.sqrt(sum((x - sum(v) / len(v)) ** 2 for x in v) / (len(v) - 1))),
    "difference": (lambda n: n == 2, lambda v: v[0] - v[1]),
    "abs_difference": (lambda n: n == 2, lambda v: abs(v[0] - v[1])),
    "ratio": (lambda n: n == 2, lambda v: v[0] / v[1]),
    # inputs[0] is the baseline: (baseline - new) / baseline * 100  (lower-is-better metrics)
    "relative_improvement": (lambda n: n == 2, lambda v: (v[0] - v[1]) / v[0] * 100.0),
    "relative_change": (lambda n: n == 2, lambda v: (v[1] - v[0]) / v[0] * 100.0),
}
_LOCK = ["uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "environment.yml", "conda-lock.yml", "package-lock.json"]


def _art(p: Project, aid: str) -> Dict[str, Any]:
    from .runs import get_artifact
    return get_artifact(p, aid)


def _jsonl(p: Project, name: str) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(p.path(name), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


# ============================================================================== metric resolution
def resolve_metric(p: Project, run: str, name: str, step: Optional[int] = None, select: Optional[str] = None) -> Dict[str, Any]:
    """Read one recorded value. Never guesses: ambiguity, NaN, failed runs and absent data are explicit."""
    out: Dict[str, Any] = {"run": run, "metric": name, "step": step, "select": select, "value": None, "unit": None,
                           "status": "verified", "issues": []}
    r = p.q1("SELECT id, status, experiment_id FROM runs WHERE id=?", (run,))
    if not r:
        return {**out, "status": "missing", "issues": [f"run {run} not found"]}
    out["experiment_id"] = r["experiment_id"]
    if r["status"] != "completed":
        return {**out, "status": "unverifiable", "issues": [f"run {run} is {r['status']}, not completed"]}
    rows = p.q("SELECT value, value_text, step, unit FROM metrics WHERE run_id=? AND name=? ORDER BY COALESCE(step,-1), id", (run, name))
    if not rows:
        return {**out, "status": "missing", "issues": [f"metric {name!r} not recorded for {run}"]}
    if step is not None:
        rows = [x for x in rows if x["step"] == step]
        if not rows:
            return {**out, "status": "missing", "issues": [f"{name} has no value at step {step} in {run}"]}
    steps = sorted({x["step"] for x in rows}, key=lambda s: -1 if s is None else s)
    if select:
        if select not in SELECT:
            raise ResearchError(f"select must be one of {', '.join(SELECT)}")
        vals = [x for x in rows if isinstance(x["value"], (int, float))]
        pick = {"first": rows[0], "last": rows[-1]}.get(select) or (
            (min if select == "min" else max)(vals, key=lambda x: x["value"]) if vals else None)
        rows = [pick] if pick else []
    elif len(steps) > 1:
        return {**out, "status": "unverifiable", "issues": [f"{name} was logged at {len(steps)} steps; give step or select ({'|'.join(SELECT)})"]}
    elif len({repr(x["value"]) for x in rows}) > 1:
        return {**out, "status": "unverifiable", "issues": [f"{name} logged {len(rows)} different values at one step; give select"]}
    if not rows:
        return {**out, "status": "unverifiable", "issues": [f"no numeric values for {name}"]}
    m = rows[-1]
    out["unit"], out["step"] = m["unit"], m["step"]
    if len({x["unit"] for x in rows}) > 1 and not select:
        return {**out, "status": "unverifiable", "issues": [f"{name} recorded with several units"]}
    v = m["value"]
    if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
        return {**out, "status": "unverifiable", "issues": [f"{name} is not a finite number ({m['value_text'] or v})"]}
    out["value"] = float(v)
    return out


def _decimals(s: Any) -> int:
    t = str(s).strip().lower()
    if "e" in t:
        t = t.split("e")[0]
    return len(t.split(".")[1]) if "." in t else 0


def _matches(actual: float, claim: float, decimals: Optional[int], rel_tol: Optional[float]) -> bool:
    if rel_tol is not None:
        return math.isclose(actual, claim, rel_tol=rel_tol, abs_tol=1e-12)
    return abs(actual - claim) <= 0.5 * 10 ** (-(decimals or 0)) + 1e-12


# ============================================================================== bindings
def _all_bindings(p: Project) -> List[Dict[str, Any]]:
    return [r for r in _jsonl(p, "evidence.jsonl") if r.get("kind") in ("metric", "derived")]


def list_bindings(p: Project, fid: str, include_superseded: bool = False) -> List[Dict[str, Any]]:
    fid = normalize_id(fid, "F")
    rows = [r for r in _all_bindings(p) if r["finding_id"] == fid]
    dead = {r["supersedes"] for r in rows if r.get("supersedes")}
    return rows if include_superseded else [r for r in rows if r["id"] not in dead]


@atomic
def bind(p: Project, fid: str, run: Optional[str] = None, metric: Optional[str] = None, step: Optional[int] = None,
         select: Optional[str] = None, claim: Any = None, unit: Optional[str] = None, derive: Optional[str] = None,
         inputs: Any = None, factor: Optional[float] = None, rel_tol: Optional[float] = None,
         artifact: Optional[str] = None, partition: Optional[str] = None, note: Optional[str] = None,
         supersedes: Optional[str] = None) -> Dict[str, Any]:
    """Create a binding. Without `claim`, the value is READ from the evidence (recommended). With `claim`, the typed
    value is stored and compared at verification time, so a wrong number is caught rather than trusted."""
    fid = normalize_id(fid, "F")
    p.get("finding", fid)
    if rel_tol is not None and not 0 < rel_tol <= MAX_REL_TOL:
        raise ResearchError(f"rel_tol must be in (0, {MAX_REL_TOL}]: a loose tolerance can hide a wrong number")
    if partition and partition not in PARTITIONS:
        raise ResearchError(f"partition must be one of {', '.join(PARTITIONS)}")
    if derive:
        if derive not in OPS:
            raise ResearchError(f"derive must be one of {', '.join(OPS)} (no free-form expressions)")
        ins = []
        for x in as_list(inputs):
            r_, _, m_ = str(x).partition(":")
            ins.append({"run": normalize_id(r_, "RUN"), "metric": m_.split("@")[0], "step": int(m_.split("@")[1]) if "@" in m_ else None})
        if not OPS[derive][0](len(ins)):
            raise ResearchError(f"{derive} got {len(ins)} inputs, which is the wrong number")
        src = {"op": derive, "inputs": ins}
        kind = "derived"
    else:
        if not (run and metric):
            raise ResearchError("A binding needs --run and --metric, or --derive with --input RUN:metric …")
        src = {"run": normalize_id(run, "RUN"), "metric": metric, "step": step, "select": select}
        kind = "metric"
    rec: Dict[str, Any] = {"kind": kind, "finding_id": fid, "source": src, "claim": {"unit": unit, "factor": factor},
                           "rel_tol": rel_tol, "partition": partition, "note": note, "supersedes": supersedes,
                           "created_at": now_iso(), **p.author}
    if artifact:
        a = _art(p, artifact)
        rec["artifact"] = {"id": a["id"], "hash": a.get("hash"), "size": a.get("size")}
    existing = list_bindings(p, fid, True)
    if supersedes and supersedes not in {b["id"] for b in existing}:
        raise NotFound(f"binding {supersedes} not found on {fid}")
    n = len(existing) + 1
    rec["id"] = f"{fid}/B{n}"
    res = _compute(p, rec)
    if claim is None:
        if res["actual"] is None:
            raise ResearchError("Cannot read the value from the evidence: " + "; ".join(res["issues"]))
        rec["claim"].update(value=res["actual"] * (factor or 1.0), source="read", decimals=None)
    else:
        rec["claim"].update(value=float(claim), source="typed", decimals=_decimals(claim))
    append_jsonl(p.path("evidence.jsonl"), rec)
    p.event("finding", fid, "evidence_bound", f"{rec['id']} {kind} binding, claim {rec['claim']['value']:g} ({rec['claim']['source']})")
    return {**rec, "verification": verify_binding(p, rec)}


def _compute(p: Project, b: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve a binding's source to the actual value/unit, or explain why not. Whitelisted ops only."""
    s = b["source"]
    if b["kind"] == "metric":
        m = resolve_metric(p, s["run"], s["metric"], s.get("step"), s.get("select"))
        return {"actual": m["value"], "unit": m["unit"], "status": m["status"], "issues": m["issues"], "inputs": [m],
                "runs": [s["run"]]}
    ms = [resolve_metric(p, i["run"], i["metric"], i.get("step"), i.get("select")) for i in s["inputs"]]
    bad = [m for m in ms if m["status"] != "verified"]
    if bad:
        worst = "missing" if any(m["status"] == "missing" for m in bad) else "unverifiable"
        return {"actual": None, "unit": None, "status": worst, "issues": [i for m in bad for i in m["issues"]], "inputs": ms,
                "runs": [m["run"] for m in ms]}
    units = {m["unit"] for m in ms}
    if len(units) > 1:
        return {"actual": None, "unit": None, "status": "unverifiable", "inputs": ms, "runs": [m["run"] for m in ms],
                "issues": [f"incompatible input units: {sorted(str(u) for u in units)}"]}
    try:
        v = OPS[s["op"]][1]([m["value"] for m in ms])
    except ZeroDivisionError:
        return {"actual": None, "unit": None, "status": "unverifiable", "inputs": ms, "runs": [m["run"] for m in ms],
                "issues": ["division by zero in the declared calculation"]}
    unit = "%" if s["op"] in _REL else (None if s["op"] == "ratio" else ms[0]["unit"])
    return {"actual": v, "unit": unit, "status": "verified", "issues": [], "inputs": ms, "runs": [m["run"] for m in ms]}


def verify_binding(p: Project, b: Dict[str, Any]) -> Dict[str, Any]:
    res = _compute(p, b)
    c = b["claim"]
    out: Dict[str, Any] = {"id": b["id"], "status": res["status"], "claimed": c.get("value"), "actual": res["actual"],
                           "unit": res["unit"], "issues": list(res["issues"]), "warnings": [], "inputs": res["inputs"]}
    if res["status"] == "verified":
        actual = res["actual"] * (c.get("factor") or 1.0)
        out["actual"] = actual
        if not _matches(actual, c["value"], c.get("decimals"), b.get("rel_tol")):
            out["status"] = "mismatch"
            out["issues"].append(f"claimed {c['value']:g} but the recorded evidence gives {actual:.6g}")
        if c.get("unit") and not c.get("factor") and res["unit"] and c["unit"] != res["unit"]:
            out["status"] = "mismatch"
            out["issues"].append(f"claimed unit {c['unit']!r}, recorded unit {res['unit']!r} (declare a factor to convert)")
        elif c.get("unit") and not res["unit"]:
            out["warnings"].append(f"no recorded unit to check claimed unit {c['unit']!r}")
    # provenance of the runs involved
    for rid in dict.fromkeys(res["runs"]):
        r = p.q1("SELECT git_commit, git_dirty FROM runs WHERE id=?", (rid,))
        if r and not r["git_commit"]:
            out["warnings"].append(f"{rid}: code revision not recorded")
        elif r and r["git_dirty"]:
            out["warnings"].append(f"{rid}: run started with uncommitted changes")
    out["warnings"] += _partition_check(p, b, res, out)
    if b.get("artifact"):
        a = artifact_integrity(p, b["artifact"]["id"], bound_hash=b["artifact"].get("hash"))
        out["artifact"] = a
        if a["status"] in ("modified", "missing") and out["status"] == "verified":
            out["status"] = "stale" if a["status"] == "modified" else "missing"
            out["issues"].append(f"artifact {b['artifact']['id']} is {a['status']}")
        elif a["status"] in ("unhashed", "possibly_modified", "unavailable"):
            out["warnings"].append(f"artifact {b['artifact']['id']}: {a['status']} ({a['detail']})")
    return out


def _partition_check(p: Project, b: Dict[str, Any], res: Dict[str, Any], out: Dict[str, Any]) -> List[str]:
    """Leakage guard, using the tuning ledger when the experiment has one. Never infers validity from seeds/commits."""
    part, warns = b.get("partition"), []
    exps = {m.get("experiment_id") for m in res["inputs"] if m.get("experiment_id")}
    for e in exps:
        try:
            with open(p.path("orchestration", f"tuning-{e}.json"), encoding="utf-8") as f:
                t = json.load(f)
        except (OSError, ValueError):
            continue
        if part == "test":
            if not t.get("selected"):
                out["status"] = "mismatch" if out["status"] == "verified" else out["status"]
                out["issues"].append(f"{e}: a held-out test value is cited but no tuned configuration was selected first (leakage risk)")
            elif not t.get("test"):
                out["issues"].append(f"{e}: held-out test was never recorded via `research tuning test`")
                out["status"] = "mismatch" if out["status"] == "verified" else out["status"]
            elif t["test"].get("run") and t["test"]["run"] not in res["runs"]:
                out["warnings"].append(f"{e}: the recorded one-shot test run is {t['test']['run']}, not the cited run")
        elif part is None:
            warns.append(f"{e} has a tuning plan; declare the partition of this value (design|validation|test)")
    has_plan = any(os.path.exists(p.path("orchestration", f"tuning-{e}.json")) for e in exps)
    if part is None and not has_plan:
        warns.append("evaluation partition not declared; cannot check for test-set leakage")
    elif part == "test" and not has_plan:
        warns.append("partition 'test' is declared but no structured split/tuning metadata exists to confirm it")
    return warns


def verify_finding(p: Project, fid: str) -> Dict[str, Any]:
    fid = normalize_id(fid, "F")
    bs = [verify_binding(p, b) for b in list_bindings(p, fid)]
    if not bs:
        agg = "no_bindings"
    else:
        agg = next((s for s in ("mismatch", "missing", "stale", "unverifiable") if any(b["status"] == s for b in bs)), "verified")
    return {"finding": fid, "status": agg, "all_verified": bool(bs) and agg == "verified", "bindings": bs}


def assert_numerics_ok(p: Project, fid: str) -> Optional[Dict[str, Any]]:
    """Gate for `supported`: structured numerical findings need every binding verified. Applies to humans too."""
    v = verify_finding(p, fid)
    if v["status"] in ("no_bindings", "verified"):
        return v
    bad = [f"{b['id']} {b['status']}: {'; '.join(b['issues'])}" for b in v["bindings"] if b["status"] != "verified"]
    raise PolicyBlocked(f"{fid} can't be marked supported: its numerical evidence does not verify. " + " | ".join(bad),
                        hint=f"Fix the evidence, or record a corrected binding (`research evidence bind {fid} … --supersedes <B-id>`); "
                             "a plausible explanation does not change a recorded number.")


# ============================================================================== legacy / annotation
_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?![\w])")


def suggest(p: Project, fid: str) -> Dict[str, Any]:
    """Migration aid for legacy findings: list numbers in the statement that have no binding. Nothing is auto-bound."""
    f = p.get("finding", normalize_id(fid, "F"))
    nums = [m.group(0) for m in _NUM.finditer((f.get("statement") or "") + " " + (f.get("title") or ""))]
    bound = {f"{b['claim']['value']:g}" for b in list_bindings(p, f["id"])}
    return {"finding": f["id"], "bound": len(bound), "unbound_numbers": [n for n in nums if n.rstrip('%') not in bound],
            "hint": "Bind with `research evidence bind F-… --run RUN-… --metric NAME`. Years and counts can be ignored."}


def overview(p: Project) -> Dict[str, Any]:
    rows, structured = [], 0
    for f in p.q("SELECT id, title, status FROM findings WHERE status != 'superseded' ORDER BY id"):
        v = verify_finding(p, f["id"])
        structured += v["status"] != "no_bindings"
        rows.append({"id": f["id"], "title": f["title"], "finding_status": f["status"], "numerical": v["status"],
                     "legacy_numbers": len(suggest(p, f["id"])["unbound_numbers"]) if v["status"] == "no_bindings" else 0})
    return {"findings": rows, "structured": structured, "integrity_warnings": integrity_scan(p)}


# ============================================================================== artifact integrity
def artifact_integrity(p: Project, aid: str, bound_hash: Optional[str] = None, deep: bool = False) -> Dict[str, Any]:
    """Compare an artifact with what was registered (or pinned). Registered metadata is never rewritten.
    ok · modified · missing · unavailable (external storage not reachable) · unhashed (too large to have a hash;
    size/mtime compared) · possibly_modified · directory."""
    a = _art(p, aid)
    path = p.abspath(a["path"])
    out = {"id": a["id"], "path": a["path"], "status": "ok", "detail": "", "registered_hash": a.get("hash"), "observed_hash": None}
    if not path or not os.path.exists(path):
        parent = os.path.dirname(path or "")
        out.update(status="unavailable" if a.get("external") and not os.path.isdir(parent) else "missing",
                   detail="external storage not reachable" if a.get("external") and not os.path.isdir(parent) else "file not found")
        return out
    if os.path.isdir(path):
        return {**out, "status": "directory", "detail": "directories are not content-hashed"}
    pins = [x for x in _jsonl(p, "integrity.jsonl") if x.get("artifact") == a["id"] and x.get("kind") == "pin"]
    ref = bound_hash or a.get("hash") or (pins[0]["hash"] if pins else None)
    if ref:
        obs = sha256_file(path)  # streamed in 1 MiB chunks
        out["observed_hash"] = obs
        if obs != ref:
            out.update(status="modified", detail="content hash differs from the one recorded when it was cited")
        return out
    st = os.stat(path)
    if deep:
        out["observed_hash"] = sha256_file(path)
    if a.get("size") is not None and st.st_size != a["size"]:
        out.update(status="possibly_modified", detail=f"size {st.st_size} != registered {a['size']}; no hash to prove it")
    else:
        out.update(status="unhashed", detail="larger than artifacts.hash_max_bytes; only size/mtime are comparable (pin it with `evidence pin`)")
    return out


def pin_artifact(p: Project, aid: str) -> Dict[str, Any]:
    """Record a first-seen hash for an unhashed (large) artifact as a NEW append-only record; never edits the artifact row."""
    a = _art(p, aid)
    path = p.abspath(a["path"])
    if not path or not os.path.isfile(path):
        raise NotFound(f"{a['id']}: file not available to hash")
    if a.get("hash"):
        return {"id": a["id"], "pinned": False, "reason": "already has a registered hash"}
    rec = {"kind": "pin", "artifact": a["id"], "hash": sha256_file(path), "size": os.path.getsize(path), "ts": now_iso(), **p.author}
    append_jsonl(p.path("integrity.jsonl"), rec)
    return {"id": a["id"], "pinned": True, "hash": rec["hash"]}


def integrity_scan(p: Project) -> List[Dict[str, Any]]:
    """Historical warnings: artifacts cited by findings or bindings that have since changed or vanished."""
    cited = {r["dst_id"] for r in p.q("SELECT dst_id FROM links WHERE dst_type='artifact' AND relation IN ('supports','contradicts')")}
    cited |= {b["artifact"]["id"] for b in _all_bindings(p) if b.get("artifact")}
    out = []
    for aid in sorted(cited):
        try:
            r = artifact_integrity(p, aid)
        except NotFound:
            r = {"id": aid, "status": "missing", "detail": "artifact record not found"}
        if r["status"] in ("modified", "missing", "unavailable", "possibly_modified"):
            out.append({k: r[k] for k in ("id", "status", "detail")})
    return out


# ============================================================================== run provenance
def lockfiles(root: str) -> Dict[str, str]:
    out = {}
    for n in _LOCK:
        fp = os.path.join(root, n)
        if os.path.isfile(fp) and os.path.getsize(fp) < 20 * 1024 * 1024:
            h = sha256_file(fp)
            if h:
                out[n] = h[:16]
    return out


def provenance(p: Project, run: str) -> Dict[str, Any]:
    from .runs import get_run
    r = get_run(p, normalize_id(run, "RUN"))
    params, env = r.get("parameters") or {}, r.get("env") or {}
    seeds = {k: v for k, v in params.items() if "seed" in k.lower()}
    parts = {k: v for k, v in params.items() if any(w in k.lower() for w in ("split", "partition", "fold", "dataset"))}
    lim = []
    if not r.get("git_commit"):
        lim.append("code revision unknown (not a git repo, or recorded without one)")
    elif r.get("git_dirty"):
        lim.append("started with uncommitted changes; the commit alone does not reproduce the code (see the saved diff)")
    if not seeds:
        lim.append("no random seed recorded in parameters; stochastic results may not be reproducible")
    if not env.get("lockfiles"):
        lim.append("no environment lockfile reference was recorded for this run")
    if not parts:
        lim.append("no data split/partition identity recorded in parameters; leakage cannot be checked from this run")
    return {"run": r["id"], "status": r["status"], "commit": r.get("git_commit"), "dirty": bool(r.get("git_dirty")),
            "branch": r.get("git_branch"), "parameters": params, "seeds": seeds, "partitions": parts,
            "lockfiles": env.get("lockfiles"), "python_env": env.get("python"), "command": r.get("command"),
            "metric_units": {m["name"]: m["unit"] for m in p.q("SELECT name, unit FROM metrics WHERE run_id=?", (r["id"],))},
            "limitations": lim}


# ============================================================================== literature sources
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Za-z]{2})?/\d{7})(v\d+)?$")


def _sources(p: Project) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in _jsonl(p, "sources.jsonl"):
        if r["kind"] == "source":
            out[r["id"]] = {**r, "examined": None, "identity": r["identity"], "assessments": []}
        elif r["id"] in out:
            s = out[r["id"]]
            if r["kind"] == "identity":
                s["identity"] = "verified"
                s["identity_by"] = r["by"]
            elif r["kind"] == "examined":
                s["examined"] = {"by": r["by"], "locator": r.get("locator"), "ts": r["ts"]}
            elif r["kind"] == "assessment":
                s["assessments"].append(r)
    return out


@atomic
def source_add(p: Project, doi: Optional[str] = None, arxiv: Optional[str] = None, url: Optional[str] = None,
               title: Optional[str] = None) -> Dict[str, Any]:
    """Offline. `identity` is 'format-valid' at best until someone records that they checked it resolves to this paper."""
    if not (doi or arxiv or url):
        raise ResearchError("A source needs a DOI, arXiv id or URL")
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi or "", flags=re.I) or None
    arxiv = re.sub(r"^(arxiv:|https?://arxiv\.org/abs/)", "", arxiv or "", flags=re.I) or None
    if doi and not _DOI.match(doi):
        raise ResearchError(f"{doi!r} does not look like a DOI (10.xxxx/…)")
    if arxiv and not _ARXIV.match(arxiv):
        raise ResearchError(f"{arxiv!r} does not look like an arXiv id")
    n = sum(1 for r in _jsonl(p, "sources.jsonl") if r["kind"] == "source") + 1
    rec = {"kind": "source", "id": f"SRC-{n:03d}", "doi": doi, "arxiv": arxiv, "url": url, "title": title,
           "identity": "format-valid" if (doi or arxiv) else "unverified", "ts": now_iso(), **p.author}
    append_jsonl(p.path("sources.jsonl"), rec)
    return _sources(p)[rec["id"]]


@atomic
def source_mark(p: Project, sid: str, identity_verified: bool = False, examined: bool = False, locator: Optional[str] = None,
                finding: Optional[str] = None, verdict: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    if sid not in _sources(p):
        raise NotFound(f"Source {sid} not found")
    by = p.author.get("author_name") or p.author.get("author_type")
    if identity_verified:
        append_jsonl(p.path("sources.jsonl"), {"kind": "identity", "id": sid, "by": by, "ts": now_iso()})
    if examined:
        append_jsonl(p.path("sources.jsonl"), {"kind": "examined", "id": sid, "by": by, "locator": locator, "ts": now_iso()})
    if finding:
        if verdict not in ("supports", "partially_supports", "does_not_support"):
            raise ResearchError("verdict must be supports | partially_supports | does_not_support")
        if not _sources(p)[sid]["examined"] and not examined:
            raise ResearchError("Record that the source was examined (--examined [--locator 'Fig 3']) before judging what it supports")
        p.get("finding", normalize_id(finding, "F"))
        append_jsonl(p.path("sources.jsonl"), {"kind": "assessment", "id": sid, "finding": normalize_id(finding, "F"),
                                               "verdict": verdict, "note": note, "locator": locator, "by": by, "ts": now_iso()})
    return _sources(p)[sid]


def source_list(p: Project, finding: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = list(_sources(p).values())
    if finding:
        f = normalize_id(finding, "F")
        rows = [s for s in rows if any(a["finding"] == f for a in s["assessments"])]
    return rows
