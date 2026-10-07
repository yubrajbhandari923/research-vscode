"""Side-by-side comparison of two runs: what differed (parameters, code, environment) and what changed (metrics,
outputs). Used by `research run compare`, the RPC `compare_runs` and the VS Code compare page."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import gitinfo
from .runs import get_run, list_artifacts, summary_metrics
from .schema import normalize_id
from .store import Project


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _read(path: Optional[str], limit: int = 60_000) -> Optional[str]:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def compare_runs(p: Project, a: str, b: str) -> Dict[str, Any]:
    ra, rb = get_run(p, normalize_id(a, "RUN")), get_run(p, normalize_id(b, "RUN"))
    pa, pb = ra.get("parameters") or {}, rb.get("parameters") or {}
    params = [{"name": k, "a": pa.get(k), "b": pb.get(k), "same": pa.get(k) == pb.get(k)}
              for k in sorted(set(pa) | set(pb))]
    ma, mb = summary_metrics(p, ra["id"]), summary_metrics(p, rb["id"])
    metrics: List[Dict[str, Any]] = []
    for k in sorted(set(ma) | set(mb)):
        va = ma[k]["value"] if k in ma and ma[k]["value"] is not None else (ma[k]["value_text"] if k in ma else None)
        vb = mb[k]["value"] if k in mb and mb[k]["value"] is not None else (mb[k]["value_text"] if k in mb else None)
        na, nb = _num(va), _num(vb)
        delta = nb - na if na is not None and nb is not None else None
        rel = (delta / abs(na)) if delta is not None and na else None
        metrics.append({"name": k, "a": va, "b": vb, "delta": delta, "rel": rel,
                        "unit": (ma.get(k) or mb.get(k) or {}).get("unit")})
    meta = []
    for key, label in (("status", "status"), ("exit_code", "exit code"), ("duration_s", "duration (s)"),
                       ("backend", "backend"), ("hostname", "host"), ("slurm_job_id", "SLURM job"),
                       ("git_branch", "branch"), ("git_commit", "commit"), ("git_dirty", "uncommitted changes"),
                       ("command", "command"), ("working_dir", "working dir"), ("label", "label"),
                       ("experiment_id", "experiment"), ("started_at", "started")):
        x, y = ra.get(key), rb.get(key)
        meta.append({"name": label, "a": x, "b": y, "same": x == y})
    code = None
    if ra.get("git_commit") and rb.get("git_commit") and ra["git_commit"] != rb["git_commit"]:
        code = gitinfo.diff_commits(p.root, ra["git_commit"], rb["git_commit"])
    diffs = {}
    for side, r in (("a", ra), ("b", rb)):
        rd = p.abspath(r.get("run_dir"))
        diffs[side] = _read(os.path.join(rd, "git.diff")) if rd else None
    arts_a = {x["name"]: x for x in list_artifacts(p, run=ra["id"])}
    arts_b = {x["name"]: x for x in list_artifacts(p, run=rb["id"])}
    artifacts = [{"name": n, "a": arts_a.get(n), "b": arts_b.get(n),
                  "type": (arts_a.get(n) or arts_b.get(n))["type"],
                  "same_hash": bool(arts_a.get(n) and arts_b.get(n) and arts_a[n].get("hash")
                                    and arts_a[n].get("hash") == arts_b[n].get("hash"))}
                 for n in sorted(set(arts_a) | set(arts_b))]
    return {
        "a": {k: ra.get(k) for k in ("id", "label", "status", "experiment_id", "git_commit")},
        "b": {k: rb.get(k) for k in ("id", "label", "status", "experiment_id", "git_commit")},
        "parameters": params, "metrics": metrics, "meta": meta, "artifacts": artifacts,
        "same_commit": ra.get("git_commit") == rb.get("git_commit"),
        "code_diff": code, "uncommitted": diffs,
        "changed_parameters": [x["name"] for x in params if not x["same"]],
    }
