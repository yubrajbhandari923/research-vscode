"""Importing a results archive back into the run record it came from.

    research hpc import results-RUN-0007.tar.gz

The archive carries the project/experiment/run identity and a submission token, so the import reconciles
**the existing** run instead of creating a new one. Re-importing the same archive changes nothing: metrics
are matched on their content, artifacts on (path, run), and the event is only logged when something moved.

Everything in the archive is untrusted input: member paths are validated (no absolute paths, no `..`, no
symlinks, no devices), sizes are bounded, and nothing is extracted outside the run directory.
"""
from __future__ import annotations

import json
import os
import posixpath
import shutil
import tarfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from ..schema import RUN_TERMINAL, normalize_id
from ..store import Project, append_jsonl, read_jsonl_from
from ..util import NotFound, ResearchError, sha256_bytes, sha256_file

MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 10_000


# =============================================================================== safe extraction

def _safe_name(name: str) -> str:
    """Validate one archive member path. Raises on anything that could escape the destination."""
    if not name or name in (".", "/"):
        raise ResearchError(f"archive member has no usable name: {name!r}")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ResearchError(f"refusing an absolute path in the archive: {name!r}")
    norm = posixpath.normpath(name.replace("\\", "/"))
    if norm.startswith("../") or norm == ".." or "/../" in norm:
        raise ResearchError(f"refusing a path-traversal entry in the archive: {name!r}")
    return norm


def _members(path: str) -> List[Tuple[str, int]]:
    """(validated name, size) for every regular file in the archive."""
    out: List[Tuple[str, int]] = []
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                out.append((_safe_name(info.filename), info.file_size))
    else:
        with tarfile.open(path, "r:*") as t:
            for info in t.getmembers():
                if info.isdir():
                    continue
                if not info.isfile():
                    raise ResearchError(f"refusing a non-regular archive entry ({info.name!r}); results "
                                        "archives contain only regular files")
                out.append((_safe_name(info.name), info.size))
    if len(out) > MAX_MEMBERS:
        raise ResearchError(f"archive has {len(out)} entries, over the {MAX_MEMBERS} limit")
    for name, size in out:
        if size > MAX_MEMBER_BYTES:
            raise ResearchError(f"archive member {name!r} is {size} bytes, over the "
                                f"{MAX_MEMBER_BYTES} limit")
    return out


def _strip_root(names: List[str]) -> str:
    """A results archive may be wrapped in a single top-level directory; find it."""
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and any("/" in n for n in names):
        return tops.pop() + "/"
    return ""


def extract(path: str, dest: str) -> Dict[str, str]:
    """Extract an archive into `dest`, validating every member. Returns {relative name: absolute path}."""
    members = _members(path)
    prefix = _strip_root([n for n, _ in members])
    os.makedirs(dest, exist_ok=True)
    real_dest = os.path.realpath(dest)
    out: Dict[str, str] = {}

    def target(name: str) -> Tuple[str, str]:
        """(relative name inside dest, absolute destination). Empty rel means 'skip this member'."""
        rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
        if not rel:
            return "", ""
        full = os.path.realpath(os.path.join(dest, rel))
        # realpath both sides: on macOS /var is a symlink to /private/var, so comparing a resolved path
        # against an unresolved dest would reject (or mis-key) every member.
        if full != real_dest and not full.startswith(real_dest + os.sep):
            raise ResearchError(f"refusing to extract {name!r} outside {dest}")
        return rel, full

    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                rel, full = target(_safe_name(info.filename))
                if not rel:
                    continue
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with z.open(info) as src, open(full, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                out[rel] = full
    else:
        with tarfile.open(path, "r:*") as t:
            for info in t.getmembers():
                if not info.isfile():
                    continue
                rel, full = target(_safe_name(info.name))
                if not rel:
                    continue
                os.makedirs(os.path.dirname(full), exist_ok=True)
                src = t.extractfile(info)
                if src is None:
                    continue
                with src, open(full, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                out[rel] = full
    return out


# =============================================================================== identity

def read_manifest(path: str) -> Dict[str, Any]:
    """The results manifest, without extracting anything else."""
    members = _members(path)
    prefix = _strip_root([n for n, _ in members])
    want = prefix + "manifest.json"
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if _safe_name(info.filename) == want:
                    return json.loads(z.read(info).decode("utf-8"))
    else:
        with tarfile.open(path, "r:*") as t:
            for info in t.getmembers():
                if info.isfile() and _safe_name(info.name) == want:
                    f = t.extractfile(info)
                    if f:
                        return json.loads(f.read().decode("utf-8"))
    raise ResearchError(f"{os.path.basename(path)} has no manifest.json — it is not a Research OS results "
                        "archive", hint="Collect results with the bundle's `scripts/collect.sh`.")


def _check_identity(p: Project, man: Dict[str, Any]) -> Dict[str, Any]:
    """Match the archive to a local run record. Refuses rather than guessing."""
    run_id = man.get("run_id")
    if not run_id:
        raise ResearchError("the results manifest names no run_id")
    run_id = normalize_id(str(run_id), "RUN")
    from ..runs import get_run
    try:
        run = get_run(p, str(run_id))
    except NotFound:
        raise NotFound(f"{run_id} is not a run in this project ({p.name}).",
                       hint="Import the archive into the project the bundle was built from. The run id, "
                            "experiment and project name are in the archive's manifest.json.")
    exp_id = (man.get("experiment") or {}).get("id")
    if exp_id and run["experiment_id"] != exp_id:
        raise ResearchError(f"{run_id} belongs to {run['experiment_id']} here, but the archive says "
                            f"{exp_id}. Refusing to mix experiments.")
    token = man.get("submission_token")
    recorded = ((run.get("slurm") or {}).get("hpc") or {}).get("token")
    if token and recorded and token != recorded:
        raise ResearchError(f"the archive's submission token does not match the one recorded for {run_id}.",
                            hint="This archive came from a different submission of the same run id. "
                                 "Check which results you meant to import; nothing was changed.")
    proj = (man.get("project") or {}).get("name")
    warnings = []
    if proj and proj != p.name:
        warnings.append(f"the archive was built in project {proj!r}, this project is {p.name!r}; "
                        f"importing anyway because {run_id} matches")
    return {"run": run, "warnings": warnings}


# =============================================================================== import

def _merge_jsonl(src: str, dst: str) -> int:
    """Append lines from `src` that `dst` does not already have. Content-keyed, so re-import is a no-op."""
    have = set()
    if os.path.exists(dst):
        with open(dst, "rb") as f:
            for line in f:
                s = line.strip()
                if s:
                    have.add(sha256_bytes(s))
    added = 0
    with open(src, "rb") as f:
        new = [l.strip() for l in f if l.strip()]
    with open(dst, "ab") as f:
        for line in new:
            if sha256_bytes(line) in have:
                continue
            have.add(sha256_bytes(line))
            f.write(line + b"\n")
            added += 1
    return added


def import_archive(p: Project, path: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """Import one results archive into the run record it names. Idempotent."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise NotFound(f"no such archive: {path}")
    man = read_manifest(path)
    ident = _check_identity(p, man)
    run = ident["run"]
    run_id = run["id"]
    run_dir = p.abspath(run["run_dir"])
    os.makedirs(run_dir, exist_ok=True)

    # verify the archive's own checksums before trusting its contents
    staging = os.path.join(p.path("hpc", "incoming"), f"{run_id}-{sha256_file(path)[:12]}")
    shutil.rmtree(staging, ignore_errors=True)
    files = extract(path, staging)
    integrity = _verify_checksums(staging, files)

    result: Dict[str, Any] = {
        "run": run_id, "experiment": run["experiment_id"], "archive": os.path.basename(path),
        "archive_sha256": sha256_file(path), "remote_run_dir": man.get("remote_run_dir"),
        "slurm_job_id": man.get("slurm_job_id"), "collected_at": man.get("collected_at"),
        "warnings": list(ident["warnings"]) + integrity["warnings"],
        "integrity": integrity, "metrics_added": 0, "artifacts": [], "logs": [], "dry_run": dry_run,
        "status": None, "changed": False,
    }
    if dry_run:
        shutil.rmtree(staging, ignore_errors=True)
        result["would_import"] = sorted(files)
        return result

    try:
        # metrics / artifacts JSONL: merged by content, so a re-import adds nothing
        for name in ("metrics.jsonl", "artifacts.jsonl"):
            src = files.get(name)
            if src:
                n = _merge_jsonl(src, os.path.join(run_dir, name))
                if name == "metrics.jsonl":
                    result["metrics_added"] = n
        # logs and small outputs
        for rel, src in sorted(files.items()):
            if rel.startswith("logs/") or rel.startswith("outputs/"):
                dst = os.path.join(run_dir, "collected", rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not (os.path.exists(dst) and sha256_file(dst) == sha256_file(src)):
                    shutil.copy2(src, dst)
                    result["changed"] = True
                (result["logs"] if rel.startswith("logs/") else result["artifacts"]).append(
                    p.rel(dst))
        # remote status → the canonical run status
        st = man.get("status") or {}
        if files.get("status.json"):
            with open(files["status.json"], encoding="utf-8") as f:
                st = {**st, **json.load(f)}
        result["status"] = _reconcile(p, run_id, st, man, result)
        # large artifacts that stayed on the cluster: registered by reference
        result["referenced"] = _register_remote_artifacts(p, run_id, man, run_dir)
        from ..runs import ingest_run_files, write_run_json
        ingest_run_files(p, run_id)
        write_run_json(p, run_id)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if result["changed"]:
        p.event("run", run_id, "results_imported",
                f"{run_id} results imported from {os.path.basename(path)}"
                + (f" (slurm job {result['slurm_job_id']})" if result["slurm_job_id"] else "")
                + f": status {result['status']}, {result['metrics_added']} new metric entries")
    return result


def _verify_checksums(staging: str, files: Dict[str, str]) -> Dict[str, Any]:
    """Check each extracted file against the archive's checksums.json."""
    out: Dict[str, Any] = {"checked": 0, "mismatched": [], "unlisted": [], "warnings": []}
    cs_path = files.get("checksums.json")
    if not cs_path:
        out["warnings"].append("the archive has no checksums.json; file integrity was not verified")
        return out
    with open(cs_path, encoding="utf-8") as f:
        checks = json.load(f)
    for rel, meta in sorted(checks.items()):
        src = files.get(rel)
        if not src:
            out["mismatched"].append({"path": rel, "reason": "listed in checksums.json but not in the archive"})
            continue
        got = sha256_file(src)
        out["checked"] += 1
        if meta.get("sha256") and got != meta["sha256"]:
            out["mismatched"].append({"path": rel, "expected": meta["sha256"], "actual": got})
    for rel in sorted(files):
        if rel not in checks and rel not in ("manifest.json", "checksums.json"):
            out["unlisted"].append(rel)
    if out["mismatched"]:
        raise ResearchError(
            "the results archive failed its own integrity check: "
            + "; ".join(f"{m['path']}" for m in out["mismatched"][:5]),
            hint="The archive is damaged or was modified in transit. Re-collect it on the cluster "
                 "(`bash scripts/collect.sh`) and transfer it again. Nothing was imported.")
    if out["unlisted"]:
        out["warnings"].append(f"{len(out['unlisted'])} file(s) in the archive are not listed in "
                               "checksums.json and were imported unverified")
    return out


def _reconcile(p: Project, run_id: str, st: Dict[str, Any], man: Dict[str, Any],
               result: Dict[str, Any]) -> Optional[str]:
    """Write the remote terminal state onto the existing run record."""
    from ..runs import get_run, _update_run_row
    run = get_run(p, run_id)
    state = st.get("state")
    ch: Dict[str, Any] = {}
    if state in ("completed", "failed", "cancelled", "running", "queued") and state != run["status"]:
        ch["status"] = state
    elif state not in (None, run["status"]):
        ch["status"] = "unknown"
    for src, dst in (("exit_code", "exit_code"), ("started_at", "started_at"), ("ended_at", "ended_at"),
                     ("hostname", "hostname")):
        if st.get(src) is not None and st.get(src) != run.get(dst):
            ch[dst] = st[src]
    job = man.get("slurm_job_id") or st.get("slurm_job_id")
    if job and not run.get("slurm_job_id"):
        ch["slurm_job_id"] = str(job)
    slurm = dict(run.get("slurm") or {})
    hpc = dict(slurm.get("hpc") or {})
    hpc.update({"collected_at": man.get("collected_at"), "remote_run_dir": man.get("remote_run_dir"),
                "collected_on": man.get("collected_on"),
                "archive_sha256": result["archive_sha256"]})
    for k in ("slurm_nodelist", "slurm_state", "slurm_partition"):
        if st.get(k):
            slurm[k.replace("slurm_", "")] = st[k]
    slurm["hpc"] = hpc
    if slurm != (run.get("slurm") or {}):
        ch["slurm"] = slurm
    if not ch:
        return run["status"]
    result["changed"] = True
    with p.tx():
        _update_run_row(p, run_id, ch)
        if "status" in ch:
            p.event("run", run_id, ch["status"],
                    f"{run_id} {ch['status']} on the cluster"
                    + (f" (exit {ch.get('exit_code', run.get('exit_code'))})"
                       if ch.get("exit_code", run.get("exit_code")) is not None else ""),
                    {"author_type": "system", "author_name": None})
    if ch.get("status") in RUN_TERMINAL:
        from ..runs import _after_run_finished
        _after_run_finished(p, run_id)
    return ch.get("status", run["status"])


def _register_remote_artifacts(p: Project, run_id: str, man: Dict[str, Any], run_dir: str) -> List[Dict[str, Any]]:
    """Artifacts too big to travel: recorded as external references with their remote path and size."""
    from ..runs import register_artifact
    out = []
    for a in man.get("artifacts") or []:
        if a.get("bundled_as"):
            continue        # already imported as a local file above
        remote = a.get("remote_path") or a.get("path")
        if not remote:
            continue
        marker = os.path.join(run_dir, "remote-artifacts", os.path.basename(str(remote)) + ".remote.json")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        rec = {"remote_path": remote, "size": a.get("size"), "available": a.get("available"),
               "reason": a.get("reason"), "cluster_run_dir": man.get("remote_run_dir"),
               "description": a.get("description"), "name": a.get("name"),
               "collected_at": man.get("collected_at")}
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, sort_keys=True)
        art = register_artifact(
            p, marker, run=run_id, name=a.get("name") or os.path.basename(str(remote)),
            type="remote-reference",
            description=(a.get("description") or "")
            + f" [stays on the cluster at {remote}" + (f", {a['size']} bytes" if a.get("size") else "") + "]",
            allow_missing=True)
        out.append({"artifact": art["id"], "remote_path": remote, "size": a.get("size")})
    return out
