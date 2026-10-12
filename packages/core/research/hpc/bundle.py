"""Portable experiment bundles: an immutable execution package a human can carry to any cluster.

    research hpc bundle EXP-001            →  .research/hpc/bundles/EXP-001-RUN-0007.tar.gz

    tar -xzf EXP-001-RUN-0007.tar.gz && cd EXP-001-RUN-0007
    bash scripts/preflight.sh      # checks python, sbatch, inputs — changes nothing
    bash scripts/submit.sh         # sbatch (or runs directly when Slurm is absent)
    bash scripts/collect.sh        # → results-RUN-0007.tar.gz

    research hpc import results-RUN-0007.tar.gz     (back on the laptop)

No agent, no network and no Research OS installation is needed on the cluster: the bundle carries the
`research_runner` module, which writes status/metrics/artifacts as files and never touches SQLite.

The run id is allocated **before** the bundle is built, so the same identity travels with the code,
the Slurm job and the results archive.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import posixpath
import shlex
import shutil
import stat
import tarfile
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from .. import __version__, evidence, gitinfo
from ..backends.base import CORE_PARENT
from ..backends.slurm import build_script
from ..schema import normalize_id
from ..store import Project, _atomic_write
from ..util import ResearchError, now_iso, sha256_file, slugify

BUNDLE_SCHEMA = 1
FORMATS = ("tar.gz", "zip")
# Never packaged, whatever the config says: credentials and the research database itself.
HARD_EXCLUDE = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "*.pem", "*.key", "*.p12", "*.pfx",
                ".env", ".env.*", "credentials", "credentials.json", ".netrc", ".npmrc", ".pypirc",
                "*.keytab", ".git-credentials")


def bundles_dir(p: Project) -> str:
    return p.path("hpc", "bundles")


# =============================================================================== source snapshot

def _excluded(rel: str, name: str, patterns: List[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat):
            return True
        if "/" not in pat and (pat + "/") in (rel + "/"):
            return True
    return False


def _is_secretish(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) or name == pat for pat in HARD_EXCLUDE)


def source_files(p: Project, *, extra: Optional[List[str]] = None) -> Dict[str, Any]:
    """The source snapshot: every working-tree file that is not excluded.

    A git commit alone is not a snapshot — dirty and untracked files are what actually ran, so they are
    packaged. Credential-looking files are refused outright, and oversized files must be named explicitly.
    """
    cfg = (p.config.get("hpc") or {}).get("bundle") or {}
    patterns = list(cfg.get("exclude") or [])
    max_file = int(cfg.get("max_file_bytes") or 0)
    max_total = int(cfg.get("max_total_bytes") or 0)
    forced = {os.path.normpath(x) for x in (list(cfg.get("include") or []) + list(extra or []))}

    files: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    total = 0
    for d, dirs, names in os.walk(p.root):
        rel_d = os.path.relpath(d, p.root)
        rel_d = "" if rel_d == "." else rel_d
        dirs[:] = sorted(x for x in dirs if not _excluded(posixpath.join(rel_d, x) if rel_d else x, x, patterns))
        for name in sorted(names):
            rel = posixpath.join(rel_d, name) if rel_d else name
            if _excluded(rel, name, patterns) and os.path.normpath(rel) not in forced:
                continue
            if _is_secretish(name):
                skipped.append({"path": rel, "reason": "looks like a credential; never packaged"})
                continue
            full = os.path.join(d, name)
            if os.path.islink(full):
                skipped.append({"path": rel, "reason": "symlink (not followed)"})
                continue
            try:
                size = os.path.getsize(full)
            except OSError as e:
                skipped.append({"path": rel, "reason": f"unreadable: {e}"})
                continue
            if max_file and size > max_file and os.path.normpath(rel) not in forced:
                skipped.append({"path": rel, "reason": f"{size} bytes exceeds hpc.bundle.max_file_bytes "
                                                       f"({max_file}); list it in hpc.bundle.include to force it"})
                continue
            files.append({"path": rel, "size": size})
            total += size
    if max_total and total > max_total:
        raise ResearchError(
            f"the source snapshot is {total} bytes, over hpc.bundle.max_total_bytes ({max_total})",
            hint="Datasets belong in the cluster's shared data directory, not in a bundle. Tighten "
                 "`hpc.bundle.exclude` or raise `hpc.bundle.max_total_bytes` if the sources really are this big.")
    return {"files": files, "skipped": skipped, "total_bytes": total}


# =============================================================================== manifest

def _dataset_refs(exp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dataset references declared on the experiment as `datasets` / `dataset` parameters.

    Datasets are never packaged; the bundle records which ones the job expects to find on the cluster.
    """
    params = exp.get("parameters") or {}
    out = []
    for key in ("datasets", "dataset", "data"):
        v = params.get(key)
        if not v:
            continue
        for item in (v if isinstance(v, list) else [v]):
            out.append({"id": item, "declared_in": f"parameters.{key}"} if isinstance(item, str)
                       else {**item, "declared_in": f"parameters.{key}"})
    return out


def manifest(p: Project, exp_id: str, run_ids: List[str], *, cluster: Optional[Dict[str, Any]] = None,
             resources: Optional[Dict[str, Any]] = None, command: Optional[str] = None,
             submission_token: Optional[str] = None) -> Dict[str, Any]:
    """Everything a human or a cluster needs to identify and run this bundle. No file hashes yet."""
    from ..runs import get_run
    exp = p.get("experiment", exp_id)
    g = gitinfo.info(p.root)
    runs = [get_run(p, r) for r in run_ids]
    return {
        "bundle_schema": BUNDLE_SCHEMA,
        "research_version": __version__,
        "project": {"name": p.name,
                    "slug": ((cluster or {}).get("project") or {}).get("slug") or slugify(p.name)},
        "experiment": {
            "id": exp["id"], "title": exp["title"], "status": exp["status"],
            "question_id": exp.get("question_id"),
            "hypothesis": exp.get("hypothesis"), "method": exp.get("method"),
            "expected_outcome": exp.get("expected_outcome"),
            "success_criteria": exp.get("success_criteria"),
            "stop_conditions": exp.get("stop_conditions"),
            "parameters": exp.get("parameters") or {},
            "metrics_expected": exp.get("metrics_requested") or [],
            "artifacts_expected": exp.get("expected_artifacts") or [],
        },
        "runs": [{"id": r["id"], "command": r["command"], "parameters": r.get("parameters") or {},
                  "label": r.get("label"), "working_dir": r.get("working_dir")} for r in runs],
        "command": command or (runs[0]["command"] if runs else None),
        "provenance": {
            "git_branch": g.get("branch"), "git_commit": g.get("commit"), "git_dirty": g.get("dirty"),
            "source": "working tree, including uncommitted and untracked files (a commit alone is not a "
                      "snapshot of what ran)",
            "created_at": now_iso(), "created_by": p.author.get("author_name") or p.author.get("author_type"),
        },
        "datasets": _dataset_refs(exp),
        "resources": resources or {},
        "environment": {
            "setup": list((resources or {}).get("setup") or []),
            "python": (cluster or {}).get("python") or "python3",
            "lockfiles": evidence.lockfiles(p.root),
            "note": "The bundle carries the standalone runner only. Install the project's own dependencies "
                    "with the cluster's module system or your own environment; no internet is assumed.",
        },
        "cluster": {"name": (cluster or {}).get("name"), "transport": (cluster or {}).get("transport")}
        if cluster else None,
        "submission_token": submission_token,
        "checkpoint": {
            "restart_command": (exp.get("parameters") or {}).get("restart_command"),
            "checkpoint_dir": (exp.get("parameters") or {}).get("checkpoint_dir"),
            "note": "Research OS does not checkpoint your code. Declare `restart_command` and "
                    "`checkpoint_dir` parameters on the experiment and your submit script can resume.",
        },
    }


# =============================================================================== scripts

PREFLIGHT = """#!/bin/bash
# Checks only — changes nothing, submits nothing.
set -u
cd "$(dirname "$0")/.."
ok=0
say() {{ printf '%-34s %s\\n' "$1" "$2"; }}
bad() {{ ok=1; say "$1" "MISSING  $2"; }}

echo "Research OS bundle preflight — {exp} / {runs}"
echo
PY="${{RESEARCH_PYTHON:-{python}}}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
if command -v "$PY" >/dev/null 2>&1; then
  say "python" "$("$PY" -c 'import sys;print(sys.version.split()[0])' 2>/dev/null || echo unknown) ($PY)"
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 8) else 1)' \\
    || bad "python version" "the runner needs Python >= 3.8"
else
  bad "python" "no python3 on PATH (set RESEARCH_PYTHON)"
fi
if command -v sbatch >/dev/null 2>&1; then
  say "slurm" "sbatch $(sbatch --version 2>/dev/null | head -1)"
else
  say "slurm" "absent — submit.sh will run the job in the foreground instead"
fi
PYTHONPATH="$PWD/runner" "$PY" -c 'import research_runner' 2>/dev/null \\
  && say "bundled runner" "ok" || bad "bundled runner" "runner/research_runner.py is not importable"
[ -d source ] && say "source snapshot" "$(find source -type f | wc -l | tr -d ' ') files" \\
  || bad "source snapshot" "source/ is missing"
[ -f manifest.json ] && say "manifest" "ok" || bad "manifest" "manifest.json is missing"

echo
{dataset_checks}
if [ "$ok" = 0 ]; then echo "preflight OK — next: bash scripts/submit.sh"; else
  echo "preflight FAILED — fix the items marked MISSING above; nothing was submitted." >&2; fi
exit $ok
"""

SUBMIT = """#!/bin/bash
# Submits this bundle's run(s). Safe to inspect first; it prints what it will do.
set -eu
cd "$(dirname "$0")/.."
BUNDLE="$PWD"
mkdir -p runs
{submit_body}
"""

SUBMIT_ONE = """
# ---------------------------------------------------------------- {run}
RUN_DIR="{run_dir}"
mkdir -p "$RUN_DIR"
if [ -f "$RUN_DIR/submitted" ]; then
  echo "{run}: already submitted as job $(cat "$RUN_DIR/submitted") — refusing to submit it twice."
  echo "        Delete $RUN_DIR/submitted only if you are certain that job never ran."
elif command -v sbatch >/dev/null 2>&1; then
  echo "{run}: submitting slurm/{run}.sbatch"
  JOB=$(sbatch --parsable "$BUNDLE/slurm/{run}.sbatch")
  echo "${{JOB%%;*}}" > "$RUN_DIR/submitted"
  echo "{run}: slurm job ${{JOB%%;*}}  (squeue -j ${{JOB%%;*}})"
else
  echo "{run}: no sbatch on this host — running in the foreground (Ctrl-C to stop)"
  echo "foreground" > "$RUN_DIR/submitted"
  PY="${{RESEARCH_PYTHON:-{python}}}"
  command -v "$PY" >/dev/null 2>&1 || PY=python3
  PYTHONPATH="$BUNDLE/runner" "$PY" -m research_runner \\
      --run-dir "$RUN_DIR" --cwd "$BUNDLE/source" -- {command}
fi
"""

COLLECT = """#!/bin/bash
# Packs results for import back into Research OS. Copies nothing large and deletes nothing.
set -eu
cd "$(dirname "$0")/.."
BUNDLE="$PWD"
OUT="${{1:-$BUNDLE/..}}"
MAX_BYTES="${{RESEARCH_MAX_ARTIFACT_BYTES:-{max_artifact_bytes}}}"
PY="${{RESEARCH_PYTHON:-{python}}}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
PYTHONPATH="$BUNDLE/runner" "$PY" "$BUNDLE/scripts/collect.py" \\
    --bundle "$BUNDLE" --out "$OUT" --max-bytes "$MAX_BYTES" "$@"
"""

# collect.py runs on the cluster with nothing but the standard library.
COLLECT_PY = r'''#!/usr/bin/env python3
"""Pack one bundle's results into results-<RUN>.tar.gz. Standard library only; reads, never deletes.

Artifacts larger than --max-bytes are referenced by their remote path instead of copied, so a 200 GB
checkpoint does not have to travel home.
"""
import argparse
import hashlib
import json
import os
import tarfile


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
    ap.add_argument("--run", action="append", help="only these runs (default: all in the bundle)")
    a, _unknown = ap.parse_known_args()

    bundle = os.path.abspath(a.bundle)
    manifest = json.load(open(os.path.join(bundle, "manifest.json"), encoding="utf-8"))
    runs = a.run or [r["id"] for r in manifest["runs"]]
    dirs = manifest.get("run_dirs") or {}
    made = []
    for run_id in runs:
        run_dir = dirs.get(run_id) or os.path.join(bundle, "runs", run_id)
        if not os.path.isdir(run_dir):
            print("%s: no run directory yet — nothing to collect" % run_id)
            continue
        status = {}
        sp = os.path.join(run_dir, "status.json")
        if os.path.exists(sp):
            status = json.load(open(sp, encoding="utf-8"))
        included, referenced, checksums = [], [], {}

        def add(tar, abspath, arcname):
            tar.add(abspath, arcname=arcname)
            checksums[arcname] = {"sha256": sha256(abspath), "size": os.path.getsize(abspath)}
            included.append(arcname)

        out_path = os.path.join(os.path.abspath(a.out), "results-%s.tar.gz" % run_id)
        tmp = out_path + ".tmp"
        with tarfile.open(tmp, "w:gz") as tar:
            for name in ("status.json", "metrics.jsonl", "artifacts.jsonl", "run.json"):
                fp = os.path.join(run_dir, name)
                if os.path.exists(fp):
                    add(tar, fp, name)
            for name in sorted(os.listdir(run_dir)):
                fp = os.path.join(run_dir, name)
                if os.path.isfile(fp) and (name.endswith(".log") or name.endswith(".out")
                                           or name.endswith(".err")):
                    add(tar, fp, "logs/" + name)
            # registered artifacts: copy the small ones, reference the rest
            ap_ = os.path.join(run_dir, "artifacts.jsonl")
            if os.path.exists(ap_):
                for line in open(ap_, encoding="utf-8"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    path = rec.get("path")
                    if not path or not os.path.isfile(path):
                        referenced.append({**rec, "available": False,
                                           "reason": "not found on the cluster at collect time"})
                        continue
                    size = os.path.getsize(path)
                    if size > a.max_bytes:
                        referenced.append({**rec, "size": size, "available": True, "remote_path": path,
                                           "reason": "larger than max_bytes; left on the cluster"})
                        continue
                    add(tar, path, "outputs/" + os.path.basename(path))
                    rec_out = dict(rec)
                    rec_out["bundled_as"] = "outputs/" + os.path.basename(path)
                    referenced.append({**rec_out, "size": size, "available": True, "remote_path": path})
            result_manifest = {
                "results_schema": 1,
                "bundle_schema": manifest.get("bundle_schema"),
                "project": manifest.get("project"),
                "experiment": {k: manifest["experiment"].get(k) for k in ("id", "title")},
                "run_id": run_id,
                "submission_token": manifest.get("submission_token"),
                "slurm_job_id": status.get("slurm_job_id") or _submitted(run_dir),
                "collected_at": _now(),
                "collected_on": os.uname().nodename if hasattr(os, "uname") else None,
                "remote_run_dir": run_dir,
                "status": status,
                "included": sorted(included),
                "artifacts": referenced,
                "max_bytes": a.max_bytes,
            }
            _write_member(tar, "manifest.json", json.dumps(result_manifest, indent=2, sort_keys=True) + "\n")
            _write_member(tar, "checksums.json", json.dumps(checksums, indent=2, sort_keys=True) + "\n")
        os.rename(tmp, out_path)
        made.append(out_path)
        print("%s: wrote %s (%d file(s), %d artifact(s) referenced)"
              % (run_id, out_path, len(included), len(referenced)))
    if not made:
        raise SystemExit("nothing collected")
    print("\nImport on your laptop:  research hpc import " + " ".join(os.path.basename(m) for m in made))


def _submitted(run_dir):
    fp = os.path.join(run_dir, "submitted")
    if os.path.exists(fp):
        v = open(fp, encoding="utf-8").read().strip()
        return None if v == "foreground" else v
    return None


def _now():
    import datetime
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def _write_member(tar, name, text):
    import io
    data = text.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    tar.addfile(info, io.BytesIO(data))


if __name__ == "__main__":
    main()
'''

README = """# {exp_id} — {exp_title}

Portable Research OS execution bundle. Run it on any machine with Python 3.8+; Slurm is used when present.
No network access, no agent and no Research OS installation are required here.

- **Project:** {project}
- **Experiment:** {exp_id} — {exp_title}
- **Run id(s):** {runs}            ← allocated on the laptop; keep them, they tie results to the record
- **Source:** git `{branch}` @ `{commit}`{dirty}
- **Submission token:** `{token}`

## Steps

```bash
bash scripts/preflight.sh      # checks python, sbatch and inputs; changes nothing
bash scripts/submit.sh         # sbatch, or a foreground run where Slurm is absent
# ... wait (Slurm keeps running whether or not your laptop is awake) ...
bash scripts/collect.sh        # writes results-<RUN>.tar.gz next to this directory
```

Then, back on the laptop:

```bash
research hpc import results-{first_run}.tar.gz
```

That reconciles **the same run record** this bundle was built from — metrics, artifacts, logs, exit status
and provenance all land on {first_run}, with no duplicate run created.

## What is in here

| path | what |
|---|---|
| `manifest.json` | project/experiment/run identity, hypothesis, parameters, resources, content hashes |
| `source/` | the working-tree source snapshot (including uncommitted and untracked files) |
| `runner/` | `research_runner`, which records status/metrics/artifacts as plain files |
| `slurm/` | one `#SBATCH` script per run |
| `scripts/` | `preflight.sh`, `submit.sh`, `collect.sh`, `collect.py` |
| `runs/` | created at submission time: `status.json`, `metrics.jsonl`, logs |

## Recording metrics and artifacts from your code

```python
import sys; sys.path.insert(0, "../runner")     # or set PYTHONPATH=<bundle>/runner
from research_runner import write_status        # low-level
# or, simplest: append JSON lines yourself
import json, os
with open(os.path.join(os.environ["RESEARCH_RUN_DIR"], "metrics.jsonl"), "a") as f:
    f.write(json.dumps({{"name": "rmse", "value": 0.12, "step": 3}}) + "\\n")
```

`RESEARCH_RUN_DIR`, `RESEARCH_RUN_ID` and `RESEARCH_IN_JOB` are set for your command.

## Datasets

{datasets}

Datasets are never packaged. They must already be present on the cluster; `research hpc datasets` inspects
and stages them.

## Resuming a job that hits the wall-time limit

Research OS does not checkpoint your code. Declare `restart_command` and `checkpoint_dir` parameters on the
experiment and this bundle will carry them in `manifest.json` for your script to use.
"""


# =============================================================================== build

def _runner_files() -> List[Tuple[str, str]]:
    """(source, arcname) for the runner.

    Shipped as a top-level `research_runner` module, not as `research.runner`: the package __init__ imports
    the whole core (store, SQLite, yaml), none of which belongs on a compute node. runner.py is already
    written to be standalone — it only writes files inside its run directory.
    """
    src = os.path.join(CORE_PARENT, "research", "runner.py")
    return [(src, "runner/research_runner.py")] if os.path.isfile(src) else []


def _dataset_check_lines(datasets: List[Dict[str, Any]], shared_data: Optional[str]) -> str:
    if not datasets:
        return 'say "datasets" "none declared"\n'
    lines = []
    for ds in datasets:
        did = str(ds.get("id") or "")
        if shared_data and did:
            d = posixpath.join(shared_data, did)
            lines.append(f'if [ -d {shlex.quote(d)} ]; then say "dataset {did}" "found"; '
                         f'else bad "dataset {did}" {shlex.quote(d)}; fi')
        else:
            lines.append(f'say "dataset {did}" "declared; path not configured — check it yourself"')
    return "\n".join(lines) + "\n"


def build(p: Project, experiment: str, *, run_ids: Optional[List[str]] = None, command: Optional[str] = None,
          fmt: str = "tar.gz", cluster: Optional[Dict[str, Any]] = None,
          resources: Optional[Dict[str, Any]] = None, out_dir: Optional[str] = None,
          extra_sources: Optional[List[str]] = None, submission_token: Optional[str] = None,
          remote_prefix: Optional[str] = None,
          remote_run_dirs: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Assemble an immutable bundle for one experiment.

    Creates a run record first (so the identity is fixed before anything leaves the laptop) unless
    `run_ids` names existing runs.
    """
    from ..runs import create_run, get_run, write_run_json
    if fmt not in FORMATS:
        raise ResearchError(f"bundle format must be one of {', '.join(FORMATS)}")
    exp_id = str(normalize_id(experiment, "EXP"))
    exp = p.get("experiment", exp_id)

    if run_ids:
        runs = [get_run(p, r) for r in run_ids]
        for r in runs:
            if r["experiment_id"] != exp_id:
                raise ResearchError(f"{r['id']} belongs to {r['experiment_id']}, not {exp_id}")
    else:
        cmd = command or (exp.get("parameters") or {}).get("command")
        if not cmd:
            raise ResearchError("No command to run: pass the command after `--`, or set a `command` parameter "
                                f"on {exp_id}.",
                                hint="research hpc bundle EXP-001 -- python train.py --alpha 0.1")
        runs = [create_run(p, exp_id, command=cmd, backend="hpc-bundle", status="queued",
                           parameters=exp.get("parameters") or {},
                           notes="created for a portable HPC bundle")]
    run_ids = [r["id"] for r in runs]
    token = submission_token or hashlib.sha256(
        f"{p.name}|{exp_id}|{','.join(run_ids)}|{now_iso()}".encode()).hexdigest()[:32]

    src = source_files(p, extra=extra_sources)
    man = manifest(p, exp_id, run_ids, cluster=cluster, resources=resources, command=command,
                   submission_token=token)
    man["source"] = {"files": len(src["files"]), "bytes": src["total_bytes"],
                     "skipped": src["skipped"],
                     "root": "source/"}
    # Where each run writes status/metrics/artifacts. An SSH submission points these at the project's
    # runs/ tree; a hand-carried bundle keeps them inside itself.
    man["run_dirs"] = {r["id"]: (remote_run_dirs or {}).get(r["id"]) or "" for r in runs}

    stem = f"{exp_id}-{run_ids[0]}" if len(run_ids) == 1 else f"{exp_id}-{run_ids[0]}+{len(run_ids) - 1}"
    out_dir = out_dir or bundles_dir(p)
    os.makedirs(out_dir, exist_ok=True)
    staging = os.path.join(out_dir, f".{stem}.staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        # source snapshot
        for f in src["files"]:
            dst = os.path.join(staging, "source", f["path"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(p.root, f["path"]), dst)
        # the runner
        for s, arc in _runner_files():
            dst = os.path.join(staging, arc)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(s, dst)
        # one sbatch script per run; the job cd's into the bundle's own source copy
        os.makedirs(os.path.join(staging, "slurm"), exist_ok=True)
        py = (cluster or {}).get("python") or "python3"
        for r in runs:
            # Paths are resolved at submit time by the sbatch script itself, so the bundle can be
            # extracted anywhere. `remote_prefix` is used when we know the destination (ssh submit).
            prefix = remote_prefix or "$(cd \"$(dirname \"$0\")/..\" && pwd)"
            script = _sbatch(r, prefix, resources or {}, py, exp_id,
                             run_dir=(remote_run_dirs or {}).get(r["id"]))
            _atomic_write(os.path.join(staging, "slurm", f"{r['id']}.sbatch"), script)
        # scripts
        sdir = os.path.join(staging, "scripts")
        os.makedirs(sdir, exist_ok=True)
        shared = ((cluster or {}).get("paths") or {}).get("shared_data")
        _script(os.path.join(sdir, "preflight.sh"),
                PREFLIGHT.format(exp=exp_id, runs=", ".join(run_ids), python=py,
                                 dataset_checks=_dataset_check_lines(man["datasets"], shared)))
        _script(os.path.join(sdir, "submit.sh"),
                SUBMIT.format(submit_body="".join(
                    SUBMIT_ONE.format(run=r["id"], command=shlex.quote(r["command"] or "true"), python=py,
                                      run_dir=(remote_run_dirs or {}).get(r["id"])
                                      or f'$BUNDLE/runs/{r["id"]}')
                    for r in runs)))
        maxb = int(((p.config.get("hpc") or {}).get("collect") or {}).get("max_artifact_bytes")
                   or 32 * 1024 * 1024)
        _script(os.path.join(sdir, "collect.sh"), COLLECT.format(python=py, max_artifact_bytes=maxb))
        _script(os.path.join(sdir, "collect.py"), COLLECT_PY)
        # readme
        g = man["provenance"]
        ds_text = ("\n".join(f"- `{d.get('id')}` (declared in {d.get('declared_in')})" for d in man["datasets"])
                   or "_This experiment declares no datasets._")
        _atomic_write(os.path.join(staging, "README.md"), README.format(
            exp_id=exp_id, exp_title=exp["title"], project=p.name, runs=", ".join(run_ids),
            branch=g.get("git_branch") or "—", commit=(g.get("git_commit") or "—")[:12],
            dirty=" **(uncommitted changes are included in `source/`)**" if g.get("git_dirty") else "",
            token=token, first_run=run_ids[0], datasets=ds_text))
        # content hashes over everything staged, then the manifest last
        man["contents"] = _hash_tree(staging)
        _atomic_write(os.path.join(staging, "manifest.json"),
                      json.dumps(man, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

        archive = os.path.join(out_dir, f"{stem}.{fmt}")
        _archive(staging, archive, stem, fmt)
    finally:
        keep = os.path.join(out_dir, stem)
        shutil.rmtree(keep, ignore_errors=True)
        os.replace(staging, keep)      # keep the unpacked tree: ssh submit rsyncs it, humans can inspect it

    digest = sha256_file(archive)
    for r in runs:
        _record_bundle(p, r["id"], {"bundle": p.rel(archive), "bundle_dir": p.rel(keep), "token": token,
                                    "sha256": digest, "experiment_id": exp_id,
                                    "cluster": (cluster or {}).get("name")})
        write_run_json(p, r["id"])
    p.event("experiment", exp_id, "bundled",
            f"{exp_id} bundled for portable execution: {p.rel(archive)} ({', '.join(run_ids)})")
    return {"path": p.rel(archive), "dir": p.rel(keep), "experiment": exp_id, "runs": run_ids,
            "token": token, "sha256": digest, "format": fmt,
            "bytes": os.path.getsize(archive), "manifest": man}


def _sbatch(run: Dict[str, Any], prefix: str, resources: Dict[str, Any], python: str, exp_id: str,
            run_dir: Optional[str] = None) -> str:
    """An #SBATCH script that resolves its own paths, so the bundle works wherever it is extracted."""
    opts = {**resources, "job_name": f"{exp_id}-{run['id']}"}
    # Reuse the existing generator for the #SBATCH header, then replace its body with a relocatable one.
    header = build_script("__RUN_DIR__", "__CMD__", "__CWD__", run["id"], opts, python=python)
    head = header.split("\n# generated by research", 1)[0].replace("#SBATCH --output=__RUN_DIR__/", "#SBATCH --output=")
    head = head.replace("#SBATCH --output=slurm-%j.out", "#SBATCH --output=slurm-%j.out")
    # One run directory per run, wherever the caller says. `run_dir` lets an SSH submission put it in the
    # project's runs/ tree (so the receipt and status.json live together) while the code snapshot stays
    # immutable; a hand-carried bundle falls back to its own runs/ subdirectory.
    rd = run_dir or ("$BUNDLE/runs/" + run["id"])
    lines = [head.rstrip(), "",
             "# generated by research — do not edit; re-bundle instead",
             f'BUNDLE={prefix}',
             f'RUN_DIR="{rd}"',
             'mkdir -p "$RUN_DIR"',
             'cd "$BUNDLE/source"']
    for s in resources.get("setup") or []:
        lines.append(str(s))
    lines += ['export PYTHONPATH="$BUNDLE/runner${PYTHONPATH:+:$PYTHONPATH}"',
              f'exec {shlex.quote(python)} -m research_runner --run-dir "$RUN_DIR" --cwd "$BUNDLE/source" -- '
              + shlex.quote(run["command"] or "true")]
    return "\n".join(lines) + "\n"


def _script(path: str, text: str) -> None:
    _atomic_write(path, text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _hash_tree(root: str) -> Dict[str, Dict[str, Any]]:
    out = {}
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(dirs)
        for n in sorted(names):
            fp = os.path.join(d, n)
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            out[rel] = {"sha256": sha256_file(fp), "size": os.path.getsize(fp)}
    return out


def _archive(staging: str, archive: str, stem: str, fmt: str) -> None:
    tmp = archive + ".tmp"
    if fmt == "zip":
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for d, dirs, names in os.walk(staging):
                dirs[:] = sorted(dirs)
                for n in sorted(names):
                    fp = os.path.join(d, n)
                    z.write(fp, os.path.join(stem, os.path.relpath(fp, staging)))
    else:
        with tarfile.open(tmp, "w:gz") as t:
            for d, dirs, names in os.walk(staging):
                dirs[:] = sorted(dirs)
                for n in sorted(names):
                    fp = os.path.join(d, n)
                    t.add(fp, arcname=os.path.join(stem, os.path.relpath(fp, staging)))
    os.replace(tmp, archive)


def _record_bundle(p: Project, run_id: str, info: Dict[str, Any]) -> None:
    """Keep the bundle/submission facts on the run record itself: no second source of truth."""
    from ..runs import get_run, _update_run_row
    run = get_run(p, run_id)
    slurm = dict(run.get("slurm") or {})
    hpc = dict(slurm.get("hpc") or {})
    hpc.update({k: v for k, v in info.items() if v is not None})
    slurm["hpc"] = hpc
    with p.tx():
        _update_run_row(p, run_id, {"slurm": slurm})
