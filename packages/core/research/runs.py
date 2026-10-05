"""Runs, metrics and artifacts: lifecycle, ingestion of job-written files, status reconciliation."""
from __future__ import annotations

import json
import os
import shlex
import time
from typing import Any, Dict, List, Optional, Sequence, Union

from . import gitinfo, schema
from .backends import get_backend
from .runner import read_status
from .runner import run as runner_run
from .schema import RUN_TERMINAL, normalize_id
from .store import Project, _atomic_write, append_jsonl, read_jsonl_from
from .util import NotFound, ResearchError, as_list, hostname, jdump, jload, now_iso, parse_iso, sha256_file

RUN_JSON_FIELDS = ["id", "experiment_id", "label", "command", "working_dir", "parameters", "env", "backend",
                   "hostname", "pid", "slurm_job_id", "slurm", "run_dir", "stdout_path", "stderr_path",
                   "git_branch", "git_commit", "git_dirty", "status", "exit_code", "reviewed", "override_reason",
                   "created_at", "started_at", "ended_at", "updated_at", "author_type", "author_name",
                   "author_model", "notes", "metrics_offset", "artifacts_offset"]
JSON_COLS = {"parameters", "env", "slurm"}

EXT_TYPES = {
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "svg": "image", "webp": "image", "bmp": "image",
    "tif": "image", "tiff": "image",
    "pdf": "pdf", "csv": "table", "tsv": "table", "parquet": "table", "json": "json", "jsonl": "json",
    "yaml": "text", "yml": "text", "md": "markdown", "txt": "text", "log": "log", "out": "log", "err": "log",
    "npy": "array", "npz": "array", "nii": "nifti", "nii.gz": "nifti", "h5": "hdf5", "hdf5": "hdf5", "nc": "netcdf",
    "pt": "checkpoint", "pth": "checkpoint", "ckpt": "checkpoint", "safetensors": "checkpoint", "pkl": "pickle",
    "mp4": "video", "mov": "video", "webm": "video", "gif ": "video", "py": "code", "sh": "code", "jl": "code",
    "ipynb": "notebook", "html": "html",
}


def infer_type(path: str) -> str:
    if os.path.isdir(path):
        return "directory"
    low = path.lower()
    if low.endswith(".nii.gz"):
        return "nifti"
    ext = low.rsplit(".", 1)[-1] if "." in os.path.basename(low) else ""
    return EXT_TYPES.get(ext, "binary" if ext else "file")


# =============================================================================== run records

def _decode_run(r: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(r)
    for c in JSON_COLS:
        r[c] = jload(r.get(c), {} if c != "slurm" else None)
    r["git_dirty"] = None if r.get("git_dirty") is None else bool(r["git_dirty"])
    r["reviewed"] = bool(r.get("reviewed"))
    r["type"] = "run"
    s, e = parse_iso(r.get("started_at")), parse_iso(r.get("ended_at"))
    r["duration_s"] = (e - s).total_seconds() if s and e else None
    return r


def get_run(p: Project, run_id: str) -> Dict[str, Any]:
    run_id = normalize_id(run_id, "RUN")
    r = p.q1("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise NotFound(f"Run {run_id} not found")
    return _decode_run(r)


def list_runs(p: Project, experiment: Optional[str] = None, status: Optional[str] = None,
              limit: Optional[int] = None) -> List[Dict[str, Any]]:
    where, args = [], []
    if experiment:
        where.append("experiment_id=?")
        args.append(normalize_id(experiment, "EXP"))
    if status:
        where.append("status=?")
        args.append(status)
    sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [_decode_run(r) for r in p.q(sql, tuple(args))]


def write_run_json(p: Project, run_id: str) -> None:
    r = p.q1("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        return
    rec = {k: r.get(k) for k in RUN_JSON_FIELDS}
    for c in JSON_COLS:
        rec[c] = jload(rec.get(c), None)
    d = p.abspath(r["run_dir"])
    os.makedirs(d, exist_ok=True)
    _atomic_write(os.path.join(d, "run.json"), json.dumps(rec, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _update_run_row(p: Project, run_id: str, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    enc = {k: (jdump(v) if k in JSON_COLS else (int(v) if isinstance(v, bool) else v)) for k, v in fields.items()}
    enc["updated_at"] = now_iso()
    sets = ",".join(f"{k}=?" for k in enc)
    p.conn.execute(f"UPDATE runs SET {sets} WHERE id=?", (*enc.values(), run_id))


def command_str(command: Union[str, Sequence[str], None]) -> Optional[str]:
    if command is None:
        return None
    if isinstance(command, str):
        return command
    cmd = list(command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if len(cmd) == 1:
        return cmd[0]
    return " ".join(shlex.quote(c) for c in cmd)


def create_run(p: Project, experiment: str, command: Union[str, Sequence[str], None] = None,
               parameters: Optional[Dict[str, Any]] = None, label: Optional[str] = None,
               working_dir: Optional[str] = None, backend: str = "manual", status: str = "queued",
               notes: Optional[str] = None, override: Optional[str] = None,
               stdout_path: Optional[str] = None, stderr_path: Optional[str] = None,
               exit_code: Optional[int] = None, started_at: Optional[str] = None,
               ended_at: Optional[str] = None, host: Optional[str] = None,
               slurm_job_id: Optional[str] = None) -> Dict[str, Any]:
    """Register a run under an experiment (budget-checked). Does not execute anything."""
    from .services import check_run_budget, set_experiment_status
    exp_id = normalize_id(experiment, "EXP")
    exp = p.get("experiment", exp_id)
    if status not in schema.STATUSES["run"]:
        raise ResearchError(f"Invalid run status {status!r}")
    warnings = check_run_budget(p, exp_id, override)
    g = gitinfo.info(p.root)
    run_id = p.next_id("RUN")
    run_dir = p.rel(p.path("runs", run_id))
    os.makedirs(p.abspath(run_dir), exist_ok=True)
    wd = p.rel(working_dir or os.getcwd()) if (working_dir or backend != "manual") else p.rel(os.getcwd())
    ts = now_iso()
    env = {"python": os.environ.get("CONDA_DEFAULT_ENV") or os.environ.get("VIRTUAL_ENV"),
           "user": os.environ.get("USER")}
    row = {
        "id": run_id, "experiment_id": exp_id, "label": label, "command": command_str(command),
        "working_dir": wd, "parameters": jdump(parameters or {}), "env": jdump({k: v for k, v in env.items() if v}),
        "backend": backend, "hostname": host or hostname(), "slurm_job_id": slurm_job_id, "run_dir": run_dir,
        "stdout_path": p.rel(stdout_path) if stdout_path else f"{run_dir}/stdout.log",
        "stderr_path": p.rel(stderr_path) if stderr_path else f"{run_dir}/stderr.log",
        "git_branch": g["branch"], "git_commit": g["commit"],
        "git_dirty": None if g["dirty"] is None else int(bool(g["dirty"])),
        "status": status, "exit_code": exit_code, "reviewed": 0, "override_reason": override,
        "created_at": ts, "started_at": started_at, "ended_at": ended_at, "updated_at": ts,
        **p.author, "notes": notes, "metrics_offset": 0, "artifacts_offset": 0,
    }
    with p.tx():
        cols = list(row)
        p.conn.execute(f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       tuple(row.values()))
        p.event("run", run_id, "created", f"{run_id} registered under {exp_id}"
                + (f" [{label}]" if label else "") + (f" (override: {override})" if override else ""))
    if g["dirty"]:
        d = gitinfo.diff(p.root)
        if d:
            with open(os.path.join(p.abspath(run_dir), "git.diff"), "w", encoding="utf-8") as f:
                f.write(d)
    if row["command"]:
        with open(os.path.join(p.abspath(run_dir), "command.sh"), "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n# " + run_id + " — re-run this command (paths relative to the project root)\n"
                    'cd "$(dirname "$0")/../../.." && cd ' + shlex.quote(wd or ".") + "\n" + row["command"] + "\n")
    write_run_json(p, run_id)
    if exp["status"] in ("proposed", "ready", "needs_review", "completed") and status in ("queued", "running"):
        set_experiment_status(p, exp_id, "running", f"{run_id} started")
    elif not exp.get("started_at"):
        p.update_entity("experiment", exp_id, {"started_at": ts}, log=False)
    if status in RUN_TERMINAL:
        _after_run_finished(p, run_id)
    else:
        p.export("experiment", exp_id)
    r = get_run(p, run_id)
    if warnings:
        r["warnings"] = warnings
    return r


def attach_run(p: Project, experiment: str, command: Optional[str] = None, status: str = "completed",
               **kw: Any) -> Dict[str, Any]:
    """Register a run you already executed yourself (e.g. in a terminal or notebook)."""
    return create_run(p, experiment, command=command, backend="manual", status=status, **kw)


def update_run(p: Project, run_id: str, status: Optional[str] = None, exit_code: Optional[int] = None,
               notes: Optional[str] = None, label: Optional[str] = None,
               parameters: Optional[Dict[str, Any]] = None, reviewed: Optional[bool] = None) -> Dict[str, Any]:
    run = get_run(p, run_id)
    ch: Dict[str, Any] = {}
    if status:
        if status not in schema.STATUSES["run"]:
            raise ResearchError(f"Invalid run status {status!r}")
        ch["status"] = status
        if status in RUN_TERMINAL and not run.get("ended_at"):
            ch["ended_at"] = now_iso()
        if status == "running" and not run.get("started_at"):
            ch["started_at"] = now_iso()
    if exit_code is not None:
        ch["exit_code"] = exit_code
    if notes is not None:
        ch["notes"] = notes
    if label is not None:
        ch["label"] = label
    if parameters is not None:
        ch["parameters"] = {**(run.get("parameters") or {}), **parameters}
    if reviewed is not None:
        ch["reviewed"] = bool(reviewed)
    with p.tx():
        _update_run_row(p, run["id"], ch)
        p.event("run", run["id"], "updated", f"{run['id']}: " + ", ".join(
            f"{k}={v}" for k, v in ch.items() if k in ("status", "exit_code", "label")) or f"{run['id']} updated")
    write_run_json(p, run["id"])
    if status in RUN_TERMINAL and run["status"] not in RUN_TERMINAL:
        _after_run_finished(p, run["id"])
    else:
        p.export("experiment", run["experiment_id"])
    return get_run(p, run["id"])


def set_run_status(p: Project, run_id: str, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
    return update_run(p, run_id, status=status, notes=reason)


def _after_run_finished(p: Project, run_id: str) -> None:
    from .services import experiment_state, set_experiment_status
    run = get_run(p, run_id)
    exp = p.get("experiment", run["experiment_id"])
    st = experiment_state(p, exp["id"])
    if exp["status"] in ("running", "proposed", "ready") and st["active"] == 0:
        set_experiment_status(p, exp["id"], "needs_review", "all runs finished")
    else:
        p.export("experiment", exp["id"])


# =============================================================================== execution

def _new_exec_run(p: Project, experiment: str, command, backend: str, **kw: Any) -> Dict[str, Any]:
    if not command_str(command):
        raise ResearchError("No command given. Usage: research run exec EXP-001 -- python train.py …")
    return create_run(p, experiment, command=command, backend=backend, status="queued", **kw)


def exec_run(p: Project, experiment: str, command, *, detach: bool = False, parameters=None, label=None,
             working_dir=None, notes=None, override=None, tee: bool = True) -> Dict[str, Any]:
    """Create + execute a local run. Foreground by default (streams output), or detached."""
    run = _new_exec_run(p, experiment, command, "local", parameters=parameters, label=label,
                        working_dir=working_dir or os.getcwd(), notes=notes, override=override)
    if detach:
        info = get_backend("local").submit(p, run, {})
        with p.tx():
            _update_run_row(p, run["id"], {"status": "running", "pid": info["pid"], "hostname": info["hostname"],
                                           "started_at": now_iso()})
            p.event("run", run["id"], "started", f"{run['id']} started locally (pid {info['pid']})")
        write_run_json(p, run["id"])
        p.export("experiment", run["experiment_id"])
        return get_run(p, run["id"])
    with p.tx():
        _update_run_row(p, run["id"], {"status": "running", "started_at": now_iso(), "pid": os.getpid()})
    write_run_json(p, run["id"])
    runner_run(p.abspath(run["run_dir"]), run["command"], p.abspath(run["working_dir"]), tee=tee)
    return reconcile_run(p, run["id"], force=True)


def submit_run(p: Project, experiment: str, command, *, parameters=None, label=None, working_dir=None,
               notes=None, override=None, **slurm_opts: Any) -> Dict[str, Any]:
    run = _new_exec_run(p, experiment, command, "slurm", parameters=parameters, label=label,
                        working_dir=working_dir or os.getcwd(), notes=notes, override=override)
    try:
        info = get_backend("slurm").submit(p, run, slurm_opts)
    except Exception as e:
        with p.tx():
            _update_run_row(p, run["id"], {"status": "failed", "ended_at": now_iso(), "notes": f"submit failed: {e}"})
            p.event("run", run["id"], "failed", f"{run['id']} SLURM submission failed: {e}")
        write_run_json(p, run["id"])
        _after_run_finished(p, run["id"])
        raise
    with p.tx():
        _update_run_row(p, run["id"], {"slurm_job_id": info["slurm_job_id"], "slurm": info["slurm"],
                                       "status": "queued"})
        p.event("run", run["id"], "submitted", f"{run['id']} submitted to SLURM as job {info['slurm_job_id']}")
    write_run_json(p, run["id"])
    p.export("experiment", run["experiment_id"])
    return get_run(p, run["id"])


def cancel_run(p: Project, run_id: str) -> Dict[str, Any]:
    run = get_run(p, run_id)
    if run["status"] in RUN_TERMINAL:
        raise ResearchError(f"{run['id']} already {run['status']}")
    if run["backend"] in ("local", "slurm"):
        get_backend(run["backend"]).cancel(p, run)
    return update_run(p, run["id"], status="cancelled")


def reconcile_run(p: Project, run_id: str, force: bool = False) -> Dict[str, Any]:
    """Merge backend/file state into the index for one run."""
    run = get_run(p, run_id)
    ingest_run_files(p, run["id"])
    if run["status"] in RUN_TERMINAL and not force:
        return get_run(p, run["id"])
    st: Optional[Dict[str, Any]] = None
    if run["backend"] in ("local", "slurm"):
        try:
            st = get_backend(run["backend"]).status(p, run)
        except ResearchError:
            st = read_status(p.abspath(run["run_dir"]))
    else:
        st = read_status(p.abspath(run["run_dir"]))
    if not st:
        return run
    ch: Dict[str, Any] = {}
    state = st.get("state")
    if state and state != run["status"] and state in schema.STATUSES["run"]:
        ch["status"] = state
    for src, dst in (("exit_code", "exit_code"), ("started_at", "started_at"), ("ended_at", "ended_at"),
                     ("hostname", "hostname")):
        if st.get(src) is not None and st.get(src) != run.get(dst):
            ch[dst] = st[src]
    if st.get("slurm_job_id") and not run.get("slurm_job_id"):
        ch["slurm_job_id"] = st["slurm_job_id"]
    if run["backend"] == "slurm":
        s = dict(run.get("slurm") or {})
        for k in ("slurm_nodelist", "slurm_state", "slurm_partition"):
            if st.get(k):
                s[k.replace("slurm_", "")] = st[k]
        if s != (run.get("slurm") or {}):
            ch["slurm"] = s
    if ch.get("status") in RUN_TERMINAL and not (ch.get("ended_at") or run.get("ended_at")):
        ch["ended_at"] = now_iso()
    if not ch:
        return run
    with p.tx():
        _update_run_row(p, run["id"], ch)
        if "status" in ch:
            code = ch.get("exit_code", run.get("exit_code"))
            p.event("run", run["id"], ch["status"], f"{run['id']} {ch['status']}"
                    + (f" (exit {code})" if code is not None else "") + (f" — {st['note']}" if st.get("note") else ""),
                    {"author_type": "system", "author_name": None})
    write_run_json(p, run["id"])
    if ch.get("status") in RUN_TERMINAL:
        _after_run_finished(p, run["id"])
    else:
        p.export("experiment", run["experiment_id"])
    return get_run(p, run["id"])


def sync_runs(p: Project, slurm_min_interval: float = 20.0) -> None:
    """Import unknown run dirs; ingest metrics/artifacts files; reconcile active runs."""
    import_run_dirs(p)
    now = time.time()
    last = float(p.meta("slurm_checked", 0) or 0)
    check_slurm = now - last >= slurm_min_interval
    for r in p.q("SELECT id, status, backend, run_dir, metrics_offset, artifacts_offset FROM runs"):
        d = p.abspath(r["run_dir"])
        for fn, col in (("metrics.jsonl", "metrics_offset"), ("artifacts.jsonl", "artifacts_offset")):
            fp = os.path.join(d, fn)
            if os.path.exists(fp) and os.path.getsize(fp) > (r[col] or 0):
                ingest_run_files(p, r["id"])
                break
        if r["status"] not in RUN_TERMINAL:
            if r["backend"] == "slurm" and not check_slurm:
                st = read_status(d)
                if not st or st.get("state") not in RUN_TERMINAL:
                    continue
            try:
                reconcile_run(p, r["id"])
            except Exception as e:  # never let one broken run break sync
                p.event("run", r["id"], "reconcile_error", str(e), {"author_type": "system", "author_name": None})
    if check_slurm:
        p.set_meta("slurm_checked", now)


def import_run_dirs(p: Project) -> int:
    d = p.path("runs")
    n = 0
    known = {r["id"] for r in p.q("SELECT id FROM runs")}
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name in known or not schema.parse_id(name):
            continue
        rj = os.path.join(d, name, "run.json")
        if not os.path.exists(rj):
            continue
        try:
            with open(rj, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        rec["id"] = name
        rec.setdefault("run_dir", p.rel(os.path.join(d, name)))
        mo, ao = rec.get("metrics_offset") or 0, rec.get("artifacts_offset") or 0
        row = {k: rec.get(k) for k in RUN_JSON_FIELDS}
        for c in JSON_COLS:
            row[c] = jdump(row.get(c))
        row["metrics_offset"] = 0  # metrics are re-read from the file
        row["artifacts_offset"] = ao  # artifacts already assigned ids live in artifacts.jsonl
        for b in ("git_dirty", "reviewed"):
            row[b] = None if row.get(b) is None else int(bool(row[b]))
        cols = list(row)
        p.conn.execute(f"INSERT OR REPLACE INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       tuple(row.values()))
        p._bump_counter(name)
        ingest_run_files(p, name, write_json=False)
        _ = mo
        n += 1
    return n


# =============================================================================== metrics

def _metric_record(name: str, value: Any, step: Optional[int], unit: Optional[str], **extra: Any) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"name": str(name), "ts": now_iso()}
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, (int, float)):
        rec["value"] = value
    else:
        try:
            rec["value"] = float(value)
        except (TypeError, ValueError):
            rec["text"] = str(value)
    if step is not None:
        rec["step"] = int(step)
    if unit:
        rec["unit"] = unit
    rec.update({k: v for k, v in extra.items() if v is not None})
    return rec


def log_metric(p: Project, name: str, value: Any, step: Optional[int] = None, unit: Optional[str] = None,
               run: Optional[str] = None, experiment: Optional[str] = None) -> Dict[str, Any]:
    if run:
        r = get_run(p, run)
        rec = _metric_record(name, value, step, unit, author_type=p.author.get("author_type"))
        append_jsonl(os.path.join(p.abspath(r["run_dir"]), "metrics.jsonl"), rec)
        ingest_run_files(p, r["id"])
        p.export("experiment", r["experiment_id"])
        return {**rec, "run_id": r["id"]}
    if experiment:
        exp_id = normalize_id(experiment, "EXP")
        p.get("experiment", exp_id)
        rec = _metric_record(name, value, step, unit, experiment_id=exp_id, author_type=p.author.get("author_type"))
        append_jsonl(p.path("metrics.jsonl"), rec)
        ingest_root_files(p)
        p.export("experiment", exp_id)
        return rec
    raise ResearchError("log_metric needs a run or an experiment (or must be called inside a research run)")


def _insert_metric(p: Project, rec: Dict[str, Any], run_id: Optional[str], exp_id: Optional[str]) -> None:
    p.conn.execute(
        "INSERT INTO metrics(run_id,experiment_id,name,value,value_text,step,unit,timestamp,author_type) VALUES(?,?,?,?,?,?,?,?,?)",
        (run_id, exp_id, rec.get("name"), rec.get("value"), rec.get("text"), rec.get("step"), rec.get("unit"),
         rec.get("ts"), rec.get("author_type")))


def metrics_for_run(p: Project, run_id: str) -> List[Dict[str, Any]]:
    return p.q("SELECT * FROM metrics WHERE run_id=? ORDER BY name, step, id", (run_id,))


def summary_metrics(p: Project, run_id: str) -> Dict[str, Dict[str, Any]]:
    """Last value per metric name (by step, then insertion order)."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in p.q("SELECT * FROM metrics WHERE run_id=? ORDER BY COALESCE(step,-1), id", (run_id,)):
        out[m["name"]] = m
    return out


# =============================================================================== artifacts

def _stat_artifact(p: Project, abspath: str, do_hash: Optional[bool]) -> Dict[str, Any]:
    info: Dict[str, Any] = {"size": None, "mtime": None, "hash": None}
    if not os.path.exists(abspath):
        return info
    st = os.stat(abspath)
    info["mtime"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))
    if os.path.isdir(abspath):
        total, count = 0, 0
        for root, _d, fns in os.walk(abspath):
            for fn in fns:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                    count += 1
                except OSError:
                    pass
                if count > 20000:
                    break
        info["size"] = total
        info["preview"] = {"files": count}
        return info
    info["size"] = st.st_size
    limit = int(p.config.get("artifacts", {}).get("hash_max_bytes") or 0)
    if do_hash or (do_hash is None and st.st_size <= limit):
        info["hash"] = sha256_file(abspath)
    return info


def register_artifact(p: Project, path: str, run: Optional[str] = None, experiment: Optional[str] = None,
                      name: Optional[str] = None, type: Optional[str] = None, description: Optional[str] = None,
                      tags: Any = None, hash: Optional[bool] = None, allow_missing: bool = False) -> Dict[str, Any]:
    """Reference a file/dir produced by research work. Nothing is copied or moved."""
    ep = os.path.expanduser(str(path))
    ap = os.path.abspath(ep if os.path.isabs(ep) else os.path.join(os.getcwd(), ep))
    if not os.path.exists(ap) and not allow_missing:
        raise NotFound(f"Artifact path does not exist: {path}", hint="Use --allow-missing to register a future output.")
    stored = p.rel(ap)
    run_id = normalize_id(run, "RUN") if run else None
    exp_id = normalize_id(experiment, "EXP") if experiment else None
    if run_id:
        r = get_run(p, run_id)
        exp_id = exp_id or r["experiment_id"]
    elif exp_id:
        p.get("experiment", exp_id)
    existing = p.q1("SELECT id FROM artifacts WHERE path=? AND COALESCE(run_id,'')=? AND COALESCE(experiment_id,'')=?",
                    (stored, run_id or "", exp_id or ""))
    aid = existing["id"] if existing else p.next_id("A")
    info = _stat_artifact(p, ap, hash)
    rec = {
        "id": aid, "run_id": run_id, "experiment_id": exp_id, "name": name or os.path.basename(ap.rstrip("/")),
        "type": type or infer_type(ap), "path": stored, "external": os.path.isabs(stored),
        "description": description, "size": info["size"], "mtime": info["mtime"], "hash": info["hash"],
        "tags": as_list(tags), "preview": info.get("preview"), "created_at": now_iso(), **p.author,
    }
    if existing:
        old = get_artifact(p, aid)
        rec["created_at"] = old["created_at"]
        for k in ("description", "name"):
            if rec[k] is None:
                rec[k] = old.get(k)
    append_jsonl(p.path("artifacts.jsonl"), rec)
    with p.tx():
        ingest_root_files(p)
        p.event("artifact", aid, "updated" if existing else "registered",
                f"{aid} {rec['name']} ({rec['type']})" + (f" → {run_id or exp_id}" if (run_id or exp_id) else ""))
    if exp_id:
        p.export("experiment", exp_id)
    return get_artifact(p, aid)


def _upsert_artifact(p: Project, rec: Dict[str, Any]) -> None:
    cols = ["id", "run_id", "experiment_id", "name", "type", "path", "external", "description", "size", "mtime",
            "hash", "tags", "preview", "author_type", "author_name", "author_model", "created_at", "updated_at"]
    row = {c: rec.get(c) for c in cols}
    row["tags"] = jdump(as_list(rec.get("tags")))
    row["preview"] = jdump(rec.get("preview"))
    row["external"] = int(bool(rec.get("external")))
    row["updated_at"] = rec.get("updated_at") or rec.get("created_at")
    p.conn.execute(f"INSERT OR REPLACE INTO artifacts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   tuple(row[c] for c in cols))
    p._bump_counter(rec["id"])


def get_artifact(p: Project, aid: str) -> Dict[str, Any]:
    aid = normalize_id(aid, "A")
    r = p.q1("SELECT * FROM artifacts WHERE id=?", (aid,))
    if not r:
        raise NotFound(f"Artifact {aid} not found")
    return _decode_artifact(p, r)


def _decode_artifact(p: Project, r: Dict[str, Any]) -> Dict[str, Any]:
    r = dict(r)
    r["tags"] = jload(r.get("tags"), [])
    r["preview"] = jload(r.get("preview"), None)
    r["external"] = bool(r.get("external"))
    r["abspath"] = p.abspath(r["path"])
    r["exists"] = bool(r["abspath"] and os.path.exists(r["abspath"]))
    r["type_"] = r.get("type")
    r["kind"] = "artifact"
    return r


def list_artifacts(p: Project, run: Optional[str] = None, experiment: Optional[str] = None) -> List[Dict[str, Any]]:
    if run:
        rows = p.q("SELECT * FROM artifacts WHERE run_id=? ORDER BY id", (normalize_id(run, "RUN"),))
    elif experiment:
        rows = p.q("SELECT * FROM artifacts WHERE experiment_id=? ORDER BY id", (normalize_id(experiment, "EXP"),))
    else:
        rows = p.q("SELECT * FROM artifacts ORDER BY id DESC")
    return [_decode_artifact(p, r) for r in rows]


# =============================================================================== ingestion

def ingest_root_files(p: Project) -> None:
    """Ingest .research/{artifacts,syntheses,metrics}.jsonl beyond the last seen offset (idempotent)."""
    with p.tx():
        for fname in ("artifacts.jsonl", "syntheses.jsonl", "metrics.jsonl"):
            path = p.path(fname)
            if not os.path.exists(path):
                continue
            key = f"offset:{fname}"
            off = int(p.meta(key, 0) or 0)
            size = os.path.getsize(path)
            if size < off:  # file replaced (e.g. git checkout) → re-read all; upserts are idempotent
                off = 0
                if fname == "metrics.jsonl":
                    p.conn.execute("DELETE FROM metrics WHERE run_id IS NULL")
            if size == off:
                continue
            end = off
            for rec, pos in read_jsonl_from(path, off):
                end = pos
                if not rec:
                    continue
                if fname == "artifacts.jsonl" and rec.get("id"):
                    _upsert_artifact(p, rec)
                elif fname == "syntheses.jsonl" and rec.get("id"):
                    p.conn.execute(
                        "INSERT OR REPLACE INTO syntheses(id,experiment_id,what_happened,what_worked,what_failed,"
                        "interpretation,limitations,unresolved,next_experiment,runs_covered,author_type,author_name,"
                        "author_model,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (rec["id"], rec.get("experiment_id"), rec.get("what_happened"), rec.get("what_worked"),
                         rec.get("what_failed"), rec.get("interpretation"), rec.get("limitations"),
                         rec.get("unresolved"), rec.get("next_experiment"), jdump(as_list(rec.get("runs_covered"))),
                         rec.get("author_type"), rec.get("author_name"), rec.get("author_model"), rec.get("created_at")))
                elif fname == "metrics.jsonl":
                    _insert_metric(p, rec, None, rec.get("experiment_id"))
            p.set_meta(key, end)


def ingest_run_files(p: Project, run_id: str, write_json: bool = True) -> int:
    """Ingest metrics.jsonl / artifacts.jsonl written inside a run directory (possibly by a SLURM job)."""
    r = p.q1("SELECT id, experiment_id, run_dir, metrics_offset, artifacts_offset FROM runs WHERE id=?", (run_id,))
    if not r:
        return 0
    d = p.abspath(r["run_dir"])
    n = 0
    new_artifacts: List[Dict[str, Any]] = []
    with p.tx():
        r = p.q1("SELECT id, experiment_id, run_dir, metrics_offset, artifacts_offset FROM runs WHERE id=?", (run_id,))
        mp = os.path.join(d, "metrics.jsonl")
        if os.path.exists(mp) and os.path.getsize(mp) > (r["metrics_offset"] or 0):
            end = r["metrics_offset"] or 0
            for rec, pos in read_jsonl_from(mp, end):
                end = pos
                if rec and rec.get("name"):
                    _insert_metric(p, rec, run_id, r["experiment_id"])
                    n += 1
            p.conn.execute("UPDATE runs SET metrics_offset=? WHERE id=?", (end, run_id))
        ap = os.path.join(d, "artifacts.jsonl")
        if os.path.exists(ap) and os.path.getsize(ap) > (r["artifacts_offset"] or 0):
            end = r["artifacts_offset"] or 0
            for rec, pos in read_jsonl_from(ap, end):
                end = pos
                if rec and rec.get("path"):
                    new_artifacts.append(rec)
            p.conn.execute("UPDATE runs SET artifacts_offset=? WHERE id=?", (end, run_id))
    for rec in new_artifacts:
        try:
            register_artifact(p, rec["path"] if os.path.isabs(rec["path"]) else os.path.join(rec.get("cwd") or p.root, rec["path"]),
                              run=run_id, name=rec.get("name"), type=rec.get("type"),
                              description=rec.get("description"), tags=rec.get("tags"), allow_missing=True)
        except ResearchError:
            pass
        n += 1
    if n and write_json:
        write_run_json(p, run_id)
    return n


# In-job helpers (no DB access; used by research.log_metric inside a running job) ---------------

def in_job_run_dir() -> Optional[str]:
    d = os.environ.get("RESEARCH_RUN_DIR")
    return d if d and os.path.isdir(d) else None


def job_log_metric(name: str, value: Any, step: Optional[int] = None, unit: Optional[str] = None) -> Dict[str, Any]:
    d = in_job_run_dir()
    if not d:
        raise ResearchError("Not inside a research run")
    rec = _metric_record(name, value, step, unit, author_type="system")
    append_jsonl(os.path.join(d, "metrics.jsonl"), rec)
    return rec


def job_register_artifact(path: str, name: Optional[str] = None, description: Optional[str] = None,
                          type: Optional[str] = None, tags: Any = None) -> Dict[str, Any]:
    d = in_job_run_dir()
    if not d:
        raise ResearchError("Not inside a research run")
    rec = {"path": os.path.abspath(path), "name": name, "description": description, "type": type,
           "tags": as_list(tags), "ts": now_iso()}
    append_jsonl(os.path.join(d, "artifacts.jsonl"), rec)
    return rec
