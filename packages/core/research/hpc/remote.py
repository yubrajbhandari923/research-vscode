"""SSH + Slurm execution: doctor, submit, status, collect, sync, inspect.

The laptop holds the only authoritative research history. The cluster holds execution state — code
snapshots, run directories, results — described by small manifests in its own directories. Information
flows one way (execution package out, results in); there are never two mutable `.research/` databases.

**Duplicate submission.** The expensive failure mode is `sbatch` succeeding and the SSH connection dying
before we read the job id. To make that recoverable:

  1. the run id and a unique submission token exist *before* anything is sent
  2. the sbatch script writes the token and job id into a remote receipt as its first action
  3. a submit that cannot confirm its job id does **not** retry — it records `submission_uncertain`
     and tells you to run `research hpc sync`, which reads the receipt (and `squeue`/`sacct`) and
     adopts the job that is already running

A transient SSH failure is never treated as proof that submission failed.
"""
from __future__ import annotations

import json
import os
import posixpath
import shlex
from typing import Any, Dict, List, Optional, Tuple

from .. import __version__
from ..schema import RUN_TERMINAL, normalize_id
from ..store import Project
from ..util import NotFound, ResearchError, now_iso
from . import bundle as B
from . import config as C
from . import datasets as D
from .transport import Ssh, connect

RECEIPT = "submission.json"
REQUIRED_TOOLS = ("sbatch", "squeue", "sacct")
OPTIONAL_TOOLS = ("scancel", "rsync", "python3")


# =============================================================================== doctor

def doctor(p: Project, cluster_name: Optional[str] = None) -> Dict[str, Any]:
    """Can this project execute on this cluster right now? Read-only; creates nothing."""
    cluster = C.get(p, cluster_name)
    out: Dict[str, Any] = {"cluster": cluster["name"], "transport": cluster.get("transport"),
                           "ssh_host": cluster.get("ssh_host"), "checks": [], "ok": False,
                           "usable_modes": []}

    def add(name: str, ok: Optional[bool], detail: str, fix: str = "") -> None:
        out["checks"].append({"check": name, "ok": ok, "detail": detail, "fix": fix})

    # 1. configuration validity, before touching the network
    try:
        paths = C.paths(p, cluster)
        add("cluster profile", True, f"workspace {paths['workspace_root']}, project dir {paths['project']}")
    except ResearchError as e:
        add("cluster profile", False, str(e), e.hint or "")
        out["summary"] = "the cluster profile is incomplete; nothing was contacted"
        return out
    if not cluster.get("ssh_host"):
        add("ssh host", False, "no ssh_host configured",
            "Set `hpc.clusters.<name>.ssh_host` to an alias you can already `ssh` to")
        out["summary"] = "no SSH host configured"
        return out

    ssh = connect(cluster)
    alive = ssh.alive()
    if not alive.ok:
        add("ssh connectivity", False, f"`ssh {cluster['ssh_host']} true` failed: "
                                       f"{alive.output.strip()[-300:] or alive.returncode}",
            f"Check it by hand: ssh {cluster['ssh_host']}. Research OS uses BatchMode, so it cannot "
            "answer a password or MFA prompt — use a key or an agent.")
        out["summary"] = "cannot reach the login node"
        out["usable_modes"] = ["bundle"]
        return out
    add("ssh connectivity", True, f"reached {cluster['ssh_host']} in {alive.seconds:.1f}s")

    # 2. remote tooling
    found = ssh.which(*(REQUIRED_TOOLS + OPTIONAL_TOOLS))
    slurm_ok = all(found.get(t) for t in REQUIRED_TOOLS)
    fix = ""
    if not slurm_ok:
        # Sites commonly ship Slurm as a module. Say so, and check whether one exists, rather than
        # leaving the user to guess why an interactive ssh finds sbatch and this did not.
        mods = ssh.run_shell("module avail 2>&1 | tr ' ' '\n' | grep -i '^slurm' | head -3")
        names = [x for x in mods.stdout.split() if x]
        fix = ("Slurm is not on PATH for this connection; a bundle can still be run by hand. "
               + (f"A module looks available ({', '.join(names)}) — add `module load {names[0]}` to this "
                  "cluster's `slurm.setup`, and check `login_shell` is not disabled."
                  if names else
                  "If an interactive `ssh` finds sbatch, your profile sets it up: keep `login_shell: true` "
                  "for this cluster, or add the right `module load` line to `slurm.setup`."))
    add("slurm", slurm_ok,
        ", ".join(f"{t}={'yes' if found.get(t) else 'NO'}" for t in REQUIRED_TOOLS), fix)
    add("scancel", bool(found.get("scancel")), "present" if found.get("scancel") else "absent",
        "" if found.get("scancel") else "`research hpc cancel` will not work without it")
    add("rsync", bool(found.get("rsync")), "present" if found.get("rsync") else "absent; scp will be used")

    # 3. python for the runner
    py = cluster.get("python") or found.get("python3") or "python3"
    ver = ssh.run_shell(f"{shlex.quote(py)} -c "
                        "'import sys;print(\"%d.%d\"%sys.version_info[:2])' 2>/dev/null")
    pyver = ver.stdout.strip()
    py_ok = bool(pyver) and tuple(int(x) for x in pyver.split(".")[:2]) >= (3, 8)
    add("python", py_ok, f"{py} → {pyver or 'not found'}" + ("" if py_ok else " (need >= 3.8)"),
        "" if py_ok else "Set `hpc.clusters.<name>.python`, or load a module in `slurm.setup`.")

    # 4. storage
    storage_ok = True
    for key in ("workspace_root", "projects", "shared_data", "scratch"):
        if key not in paths:
            continue
        t = ssh.test_dir(paths[key])
        if not t["exists"]:
            parent_ok = ssh.test_dir(posixpath.dirname(paths[key]) or "/")["writable"]
            add(f"path {key}", None if parent_ok else False,
                f"{paths[key]} does not exist yet"
                + (" (its parent is writable; `research hpc init` will create it)" if parent_ok
                   else " and its parent is not writable"),
                "" if parent_ok else "Ask for an approved writable location and set it in config.yaml")
            storage_ok = storage_ok and parent_ok
        else:
            writable = t["writable"] or key == "shared_data"   # shared data may be read-only on purpose
            add(f"path {key}", writable,
                f"{paths[key]} exists, {'writable' if t['writable'] else 'read-only'}"
                + (" (read-only is fine for shared data)" if key == "shared_data" and not t["writable"] else ""),
                "" if writable else "This path must be writable for Research OS to run jobs there")
            storage_ok = storage_ok and writable

    # 5. project deployment state
    proj = ssh.test_dir(paths["project"])
    add("project workspace", None, f"{paths['project']}: "
        + ("present" if proj["exists"] else "not deployed yet (`research hpc init` creates it)"))

    modes = ["bundle"]
    if slurm_ok and py_ok and storage_ok:
        modes.insert(0, "ssh-slurm")
    elif py_ok and storage_ok:
        modes.insert(0, "ssh-direct")
    out["usable_modes"] = modes
    out["ok"] = all(c["ok"] is not False for c in out["checks"])
    out["summary"] = ("ready: " + ", ".join(modes)) if out["ok"] else \
        "not ready — see the failing checks above; portable bundles still work"
    return out


def init_workspace(p: Project, cluster_name: Optional[str] = None, *, dry_run: bool = False) -> Dict[str, Any]:
    """Create the project's remote directories. Creates only; never deletes or overwrites."""
    cluster = C.get(p, cluster_name)
    paths = C.paths(p, cluster)
    want = [paths["project"]] + [paths[k] for k in ("code", "runs", "results", "metadata")]
    ssh = connect(cluster)
    allowed = [paths["workspace_root"], paths["projects"]]
    for w in want:
        C.check_inside(allowed, w, "remote project directory")
    existing = {w: ssh.test_dir(w)["exists"] for w in want}
    if dry_run:
        return {"cluster": cluster["name"], "paths": paths, "would_create": [w for w, e in existing.items() if not e],
                "existing": [w for w, e in existing.items() if e], "dry_run": True}
    ssh.mkdir(*want)
    meta = {"project": p.name, "slug": C.slug(p, cluster), "created_at": now_iso(),
            "research_version": __version__,
            "note": "Execution state only. The authoritative research history lives on the laptop."}
    ssh.write_file(posixpath.join(paths["metadata"], "project.json"),
                   json.dumps(meta, indent=2, sort_keys=True) + "\n")
    p.event("project", None, "hpc_init",
            f"remote workspace ready on {cluster['name']}: {paths['project']}")
    return {"cluster": cluster["name"], "paths": paths, "created": [w for w, e in existing.items() if not e],
            "existing": [w for w, e in existing.items() if e], "dry_run": False}


# =============================================================================== submission

def reservation(resources: Dict[str, Any], cluster: Dict[str, Any]) -> Dict[str, Any]:
    """What a submission would reserve, in the terms a human approves: cores, GPUs, memory, wall-time.

    Slurm bills the *reservation*, not the usage: a job asking for 8 GPUs for 24h costs that whether or
    not it uses them. This is the number the user has to see before anything is submitted.
    """
    def _int(v):
        try:
            return int(str(v).split(":")[-1])
        except (TypeError, ValueError):
            return None

    cpus = _int(resources.get("cpus")) or 1
    gpus = _int(resources.get("gpus")) or 0
    time = str(resources.get("time") or "")
    hours = _hours(time)
    out = {
        "cpus": cpus, "gpus": gpus, "mem": resources.get("mem"), "time": time or None,
        "hours": round(hours, 2) if hours is not None else None,
        "partition": resources.get("partition"), "account": resources.get("account"),
        "cpu_hours": round(cpus * hours, 1) if hours is not None else None,
        "gpu_hours": round(gpus * hours, 1) if (hours is not None and gpus) else None,
        "cluster": cluster.get("name"),
    }
    out["summary"] = (
        f"{cpus} CPU{'s' if cpus != 1 else ''}"
        + (f" · {gpus} GPU{'s' if gpus != 1 else ''}" if gpus else " · no GPU")
        + (f" · {out['mem']} memory" if out["mem"] else "")
        + (f" · up to {time} wall-time" if time else " · no wall-time limit set"))
    worst = []
    if out["cpu_hours"]:
        worst.append(f"{out['cpu_hours']} CPU-hours")
    if out["gpu_hours"]:
        worst.append(f"{out['gpu_hours']} GPU-hours")
    out["worst_case"] = " and ".join(worst) or None
    return out


def _hours(t: str) -> Optional[float]:
    from .config import _hours as _parse
    return _parse(t) if t else None


def _hpc_state(run: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((run.get("slurm") or {}).get("hpc")) or {})


def _set_hpc_state(p: Project, run_id: str, **fields: Any) -> None:
    from ..runs import _update_run_row, get_run
    run = get_run(p, run_id)
    slurm = dict(run.get("slurm") or {})
    hpc = dict(slurm.get("hpc") or {})
    hpc.update(fields)
    slurm["hpc"] = hpc
    with p.tx():
        _update_run_row(p, run_id, {"slurm": slurm})


def _remote_run_paths(paths: Dict[str, str], exp_id: str, run_id: str) -> Dict[str, str]:
    return {
        "code": posixpath.join(paths["code"], exp_id, run_id),
        "run": posixpath.join(paths["runs"], run_id),
        "results": posixpath.join(paths["results"], run_id),
    }


def submit(p: Project, experiment: str, *, cluster_name: Optional[str] = None, command: Optional[str] = None,
           dry_run: bool = False, approve: bool = False, resources: Optional[Dict[str, Any]] = None,
           stage_datasets: bool = False, deep_verify: bool = False) -> Dict[str, Any]:
    """Bundle, upload and submit one experiment run through SSH + Slurm.

    The run record and submission token exist before anything is transferred, so results always come home
    to the right record even if the connection dies mid-flight.
    """
    cluster = C.get(p, cluster_name)
    exp_id = str(normalize_id(experiment, "EXP"))
    exp = p.get("experiment", exp_id)
    paths = C.paths(p, cluster)
    merged = C.check_resources(cluster, resources or {})
    ssh = connect(cluster)

    # 1. datasets must be resolved before an expensive submission
    wanted = B._dataset_refs(exp)
    ds = {"ok": True, "datasets": [], "paths": {}, "missing": []}
    if wanted:
        ds = D.resolve(p, cluster, wanted, ssh=ssh, deep=deep_verify)
        if not ds["ok"] and not stage_datasets:
            raise ResearchError(
                f"{exp_id} needs dataset(s) that are not usable on {cluster['name']}: "
                + ", ".join(ds["missing"]),
                hint="Inspect them with `research hpc datasets list`, stage them with "
                     "`research hpc datasets fetch <id> --yes`, or pass --stage-datasets to be prompted. "
                     "Nothing was submitted.")

    # 2. the run record and token come first
    from ..runs import create_run, get_run
    cmd = command or (exp.get("parameters") or {}).get("command")
    if not cmd:
        raise ResearchError(f"no command to run: pass it after `--`, or set a `command` parameter on {exp_id}",
                            hint="research hpc submit EXP-001 --cluster duke -- python train.py")
    if dry_run:
        return {"dry_run": True, "cluster": cluster["name"], "experiment": exp_id, "command": cmd,
                "resources": merged, "reservation": reservation(merged, cluster), "datasets": ds,
                "remote": _remote_run_paths(paths, exp_id, "RUN-next"),
                "would": ["create a run record and submission token",
                          "build an immutable bundle of the working tree",
                          f"upload it to {paths['code']}/{exp_id}/<RUN>",
                          "submit it with sbatch and record the job id",
                          "leave everything else on the cluster untouched"],
                "note": "Nothing was created, transferred or submitted."}
    if not (approve or (cluster.get("execution") or {}).get("auto_submit")):
        res = reservation(merged, cluster)
        raise ResearchError(
            f"submitting {exp_id} to {cluster['name']} would reserve {res['summary']}"
            + (f" (up to {res['worst_case']})" if res["worst_case"] else "") + " — this needs approval.",
            hint="Show the user these numbers and get an explicit yes before re-running with --yes. "
                 "`research hpc submit … --dry-run` prints the full reservation and changes nothing. "
                 "Slurm bills the reservation, not the usage.")

    run = create_run(p, exp_id, command=cmd, backend="slurm", status="queued",
                     parameters=exp.get("parameters") or {},
                     notes=f"submitted to {cluster['name']} over ssh")
    run_id = run["id"]
    rp = _remote_run_paths(paths, exp_id, run_id)
    for path in rp.values():
        C.check_inside([paths["workspace_root"], paths["projects"]], path, "remote run path")

    # 3. an immutable bundle, with remote paths baked in
    bun = B.build(p, exp_id, run_ids=[run_id], cluster=cluster, resources=merged,
                  remote_prefix=shlex.quote(rp["code"]),
                  remote_run_dirs={run_id: rp["run"]})
    token = bun["token"]
    _set_hpc_state(p, run_id, cluster=cluster["name"], ssh_host=cluster["ssh_host"], token=token,
                   remote_code=rp["code"], remote_run_dir=rp["run"], remote_results=rp["results"],
                   submitted_at=None, state="uploading", datasets=ds.get("paths") or {})

    # 4. upload; an existing identical snapshot is never overwritten (code is immutable once submitted)
    already = ssh.test_dir(rp["code"])
    if already["exists"]:
        remote_token = (ssh.read_file(posixpath.join(rp["code"], ".bundle-token")) or "").strip()
        if remote_token and remote_token != token:
            raise ResearchError(
                f"{rp['code']} already holds a different code snapshot (token {remote_token[:12]}…).",
                hint="Remote code snapshots are immutable once submitted. This should not happen for a "
                     "fresh run id; check the remote directory by hand. Nothing was overwritten.")
    ssh.mkdir(rp["code"], rp["run"], rp["results"])
    ssh.push(os.path.join(p.root, bun["dir"]), rp["code"],
             method=(cluster.get("transfer") or {}).get("method", "rsync"))
    ssh.write_file(posixpath.join(rp["code"], ".bundle-token"), token + "\n")
    verified = _verify_upload(ssh, rp["code"], bun)
    _set_hpc_state(p, run_id, state="uploaded", upload_verified=verified["ok"],
                   upload_detail=verified["detail"])
    if not verified["ok"]:
        raise ResearchError(f"the uploaded code snapshot does not match the local bundle: {verified['detail']}",
                            hint=f"{run_id} was created but NOT submitted. Re-run the submission; nothing "
                                 "is running on the cluster.")

    # 5. submit — the receipt is written by the job script before anything else
    sbatch_path = posixpath.join(rp["code"], "slurm", f"{run_id}.sbatch")
    receipt = posixpath.join(rp["run"], RECEIPT)
    _set_hpc_state(p, run_id, state="submitting", receipt=receipt, submitted_at=now_iso())
    cmd_sh = (f"cd {shlex.quote(rp['code'])} && "
              f"JOB=$(sbatch --parsable {shlex.quote(sbatch_path)}) && "
              f"printf '%s' \"${{JOB%%;*}}\" > {shlex.quote(posixpath.join(rp['run'], '.jobid'))} && "
              f"printf '{{\"run_id\":\"%s\",\"token\":\"%s\",\"slurm_job_id\":\"%s\"}}\\n' "
              f"{shlex.quote(run_id)} {shlex.quote(token)} \"${{JOB%%;*}}\" > {shlex.quote(receipt)} && "
              f"printf '%s\\n' \"${{JOB%%;*}}\"")
    r = ssh.run_shell(cmd_sh, timeout=120.0)   # deliberately NOT retried: sbatch is not idempotent
    if r.ok and r.stdout.strip():
        job = r.stdout.strip().splitlines()[-1].strip()
        from ..runs import _update_run_row, write_run_json
        with p.tx():
            _update_run_row(p, run_id, {"slurm_job_id": job})
            p.event("run", run_id, "submitted",
                    f"{run_id} submitted to {cluster['name']} as slurm job {job} ({exp_id})")
        _set_hpc_state(p, run_id, state="submitted", slurm_job_id=job)
        write_run_json(p, run_id)
        return {"run": run_id, "experiment": exp_id, "cluster": cluster["name"], "slurm_job_id": job,
                "token": token, "remote": rp, "resources": merged,
                "reservation": reservation(merged, cluster), "datasets": ds,
                "bundle": bun["path"], "state": "submitted"}

    # 6. uncertain: the job may well be queued. Never resubmit; reconcile instead.
    _set_hpc_state(p, run_id, state="submission_uncertain",
                   submit_error=(r.output or "").strip()[-400:])
    p.event("run", run_id, "submission_uncertain",
            f"{run_id}: the submission to {cluster['name']} could not be confirmed "
            f"({(r.output or '').strip()[-160:]}). The job may be queued; not resubmitting.",
            {"author_type": "system", "author_name": None})
    raise ResearchError(
        f"{run_id}: could not confirm the submission to {cluster['name']}. "
        "The job may already be queued, so it was NOT resubmitted.",
        hint=f"Run `research hpc sync` — it reads the remote receipt and Slurm's own records and adopts "
             f"the job if it exists. Never re-run submit for {run_id} until sync has reported.")


def _verify_upload(ssh: Ssh, remote_code: str, bun: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a few key remote files against the bundle's recorded hashes."""
    contents = (bun.get("manifest") or {}).get("contents") or {}
    # manifest.json is written after the hashes are taken, so it has no self-hash: nothing to compare.
    want = {k: v for k, v in contents.items()
            if v.get("sha256") and (k == "README.md" or k.startswith("scripts/") or k.startswith("slurm/"))}
    if not want:
        return {"ok": True, "detail": "nothing to verify"}
    tool = ssh.which("sha256sum", "shasum")
    cmd = "sha256sum" if tool.get("sha256sum") else ("shasum -a 256" if tool.get("shasum") else None)
    if not cmd:
        listing = ssh.run_shell(f"cd {shlex.quote(remote_code)} && ls -1 " +
                                " ".join(shlex.quote(k) for k in sorted(want)) + " 2>/dev/null | wc -l")
        n = int((listing.stdout.strip() or "0").split()[0]) if listing.ok else 0
        return {"ok": n == len(want),
                "detail": f"no checksum tool on the cluster; verified {n}/{len(want)} files exist"}
    args = " ".join(shlex.quote(k) for k in sorted(want))
    r = ssh.run_shell(f"cd {shlex.quote(remote_code)} && {cmd} {args} 2>/dev/null", timeout=120.0)
    got = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 1)   # `sha  name`, and the name may contain spaces
        if len(parts) == 2:
            got[parts[1].strip().lstrip("*").lstrip("./")] = parts[0].strip()
    bad = [k for k, v in sorted(want.items()) if got.get(k) != v.get("sha256")]
    return {"ok": not bad, "detail": ("all %d file(s) match" % len(want)) if not bad
            else f"{len(bad)} mismatch(es): {', '.join(bad[:4])}"}


# =============================================================================== status / sync

def _slurm_state(ssh: Ssh, job: str) -> Dict[str, Any]:
    """Ask Slurm about one job: squeue first, then sacct. Accounting being down is not 'failed'."""
    from ..backends.slurm import map_state
    out: Dict[str, Any] = {"slurm_job_id": job, "state": None, "source": None}
    q = ssh.run_shell(f"squeue -h -j {shlex.quote(job)} -o '%T|%N|%P' 2>/dev/null", retry=True)
    line = q.stdout.strip().splitlines()[0] if q.ok and q.stdout.strip() else ""
    if line:
        f = line.split("|")
        out.update({"state": map_state(f[0]), "slurm_state": f[0], "source": "squeue"})
        if len(f) > 1 and f[1] and f[1] not in ("(null)", "n/a"):
            out["slurm_nodelist"] = f[1]
        if len(f) > 2 and f[2]:
            out["slurm_partition"] = f[2]
        return out
    a = ssh.run_shell(f"sacct -n -P -j {shlex.quote(job)} -o JobID,State,ExitCode,Start,End,NodeList "
                      "2>/dev/null", retry=True)
    if a.ok:
        for row in a.stdout.strip().splitlines():
            f = row.split("|")
            if f and f[0] == job:
                code = None
                try:
                    code = int(f[2].split(":")[0])
                except (IndexError, ValueError):
                    pass
                out.update({"state": map_state(f[1]), "slurm_state": f[1], "exit_code": code,
                            "source": "sacct"})
                for i, key in ((3, "started_at"), (4, "ended_at"), (5, "slurm_nodelist")):
                    if len(f) > i and f[i] not in ("", "Unknown", "None"):
                        out[key] = f[i]
                return out
    out["source"] = "none"
    out["note"] = ("the job is not in the queue and Slurm accounting returned nothing — it may have been "
                   "purged, or accounting may be temporarily unavailable")
    return out


def status(p: Project, run_id: str, *, cluster_name: Optional[str] = None) -> Dict[str, Any]:
    """Current remote state of one run, from the run directory and Slurm."""
    from ..runs import get_run
    run = get_run(p, run_id)
    hpc = _hpc_state(run)
    cluster = C.get(p, cluster_name or hpc.get("cluster"))
    ssh = connect(cluster)
    out: Dict[str, Any] = {"run": run["id"], "experiment": run["experiment_id"],
                           "local_status": run["status"], "cluster": cluster["name"],
                           "slurm_job_id": run.get("slurm_job_id") or hpc.get("slurm_job_id"),
                           "remote_run_dir": hpc.get("remote_run_dir"), "hpc_state": hpc.get("state")}
    rdir = hpc.get("remote_run_dir")
    if rdir:
        text = ssh.read_file(posixpath.join(rdir, "status.json"))
        if text:
            try:
                out["remote_status"] = json.loads(text)
            except ValueError:
                out["remote_status"] = None
        receipt = ssh.read_file(posixpath.join(rdir, RECEIPT))
        if receipt:
            try:
                out["receipt"] = json.loads(receipt)
            except ValueError:
                pass
        out["results_present"] = bool(ssh.test_dir(rdir)["exists"])
    job = out["slurm_job_id"]
    if job:
        out["slurm"] = _slurm_state(ssh, str(job))
    return out


def _adopt_uncertain(p: Project, ssh: Ssh, run: Dict[str, Any], hpc: Dict[str, Any]) -> Optional[str]:
    """Resolve a submission whose job id we never read. Reads the receipt the job script wrote.

    This is how a dropped connection after a successful `sbatch` is recovered — without ever
    resubmitting an expensive job.
    """
    rdir = hpc.get("remote_run_dir")
    if not rdir:
        return None
    for name in (RECEIPT, ".jobid"):
        text = ssh.read_file(posixpath.join(rdir, name))
        if not text or not text.strip():
            continue
        job = None
        if name == RECEIPT:
            try:
                rec = json.loads(text)
            except ValueError:
                continue
            if rec.get("token") and hpc.get("token") and rec["token"] != hpc["token"]:
                continue          # a different submission wrote this; never adopt it
            job = rec.get("slurm_job_id")
        else:
            job = text.strip()
        if job:
            from ..runs import _update_run_row, write_run_json
            with p.tx():
                _update_run_row(p, run["id"], {"slurm_job_id": str(job)})
                p.event("run", run["id"], "submission_recovered",
                        f"{run['id']}: the submission was confirmed after the fact — slurm job {job} "
                        "(read from the remote receipt). No job was resubmitted.",
                        {"author_type": "system", "author_name": None})
            _set_hpc_state(p, run["id"], state="submitted", slurm_job_id=str(job))
            write_run_json(p, run["id"])
            return str(job)
    return None


def collect(p: Project, run_id: str, *, cluster_name: Optional[str] = None,
            max_bytes: Optional[int] = None) -> Dict[str, Any]:
    """Pack the run's results on the cluster, bring the archive home and import it.

    Large artifacts stay on the cluster and are recorded as references. Nothing remote is deleted.
    """
    from ..runs import get_run
    from . import results as RES
    run = get_run(p, run_id)
    hpc = _hpc_state(run)
    cluster = C.get(p, cluster_name or hpc.get("cluster"))
    code = hpc.get("remote_code")
    if not code:
        raise ResearchError(f"{run['id']} has no recorded remote code directory",
                            hint="Collect results from a bundle instead: `research hpc import <archive>`.")
    ssh = connect(cluster)
    maxb = int(max_bytes if max_bytes is not None
               else ((p.config.get("hpc") or {}).get("collect") or {}).get("max_artifact_bytes")
               or 32 * 1024 * 1024)
    py = cluster.get("python") or "python3"
    out_dir = posixpath.dirname(code.rstrip("/"))
    r = ssh.run_shell(
        f"cd {shlex.quote(code)} && PYTHONPATH={shlex.quote(code)}/runner {shlex.quote(py)} "
        f"{shlex.quote(posixpath.join(code, 'scripts', 'collect.py'))} "
        f"--bundle {shlex.quote(code)} --out {shlex.quote(out_dir)} --max-bytes {maxb} "
        f"--run {shlex.quote(run['id'])}", timeout=1800.0)
    if not r.ok:
        raise ResearchError(f"collecting {run['id']} on {cluster['name']} failed: "
                            f"{r.output.strip()[-400:]}",
                            hint="Nothing was changed locally or remotely.")
    archive = posixpath.join(out_dir, f"results-{run['id']}.tar.gz")
    local_dir = p.path("hpc", "incoming")
    os.makedirs(local_dir, exist_ok=True)
    ssh.pull(archive, local_dir, method=(cluster.get("transfer") or {}).get("method", "rsync"))
    local = os.path.join(local_dir, f"results-{run['id']}.tar.gz")
    if not os.path.isfile(local):
        raise ResearchError(f"the results archive did not arrive at {local}")
    imported = RES.import_archive(p, local)
    _set_hpc_state(p, run["id"], state="collected", collected_at=now_iso(), remote_archive=archive)
    return {**imported, "cluster": cluster["name"], "remote_archive": archive,
            "local_archive": p.rel(local)}


def sync(p: Project, *, cluster_name: Optional[str] = None, collect_results: bool = True) -> Dict[str, Any]:
    """Reconcile every non-terminal remote run: adopt uncertain submissions, update status, collect results.

    Safe to run at any time, including after days offline. Idempotent: a run that is already terminal and
    collected is skipped, and re-collecting imports nothing new.
    """
    rows = p.q("SELECT id FROM runs WHERE backend IN ('slurm','hpc-bundle') ORDER BY id")
    from ..runs import get_run
    out: Dict[str, Any] = {"checked": 0, "updated": [], "collected": [], "recovered": [],
                           "needs_attention": [], "skipped": 0, "clusters": [], "errors": []}
    by_cluster: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        run = get_run(p, row["id"])
        hpc = _hpc_state(run)
        name = cluster_name or hpc.get("cluster")
        if not name:
            out["skipped"] += 1
            continue
        if run["status"] in RUN_TERMINAL and hpc.get("state") == "collected":
            out["skipped"] += 1
            continue
        by_cluster.setdefault(name, []).append(run)

    for name, runs in sorted(by_cluster.items()):
        try:
            cluster = C.get(p, name)
            ssh = connect(cluster)
            alive = ssh.alive()
            if not alive.ok:
                out["errors"].append({"cluster": name,
                                      "error": f"unreachable: {alive.output.strip()[-200:]}; "
                                               "no run state was changed"})
                continue
        except ResearchError as e:
            out["errors"].append({"cluster": name, "error": str(e)})
            continue
        out["clusters"].append(name)
        for run in runs:
            out["checked"] += 1
            try:
                _sync_one(p, ssh, cluster, run, out, collect_results)
            except ResearchError as e:
                out["errors"].append({"run": run["id"], "error": str(e)})

    out["next_tasks"] = _pending_analysis(p)
    return out


def _sync_one(p: Project, ssh: Ssh, cluster: Dict[str, Any], run: Dict[str, Any], out: Dict[str, Any],
              collect_results: bool) -> None:
    from ..runs import _update_run_row, get_run, write_run_json
    hpc = _hpc_state(run)
    job = run.get("slurm_job_id") or hpc.get("slurm_job_id")
    if not job and hpc.get("state") == "submission_uncertain":
        job = _adopt_uncertain(p, ssh, run, hpc)
        if job:
            out["recovered"].append({"run": run["id"], "slurm_job_id": job})
        else:
            out["needs_attention"].append({
                "run": run["id"], "why": "a submission could not be confirmed and no remote receipt exists",
                "do": f"check by hand whether a job for {run['id']} is queued, then either "
                      f"`research run update {run['id']} --status failed --notes 'never submitted'` "
                      f"or re-submit. Research OS will not resubmit on its own."})
            return
    if not job:
        return
    st = _slurm_state(ssh, str(job))
    ch: Dict[str, Any] = {}
    if st.get("state") and st["state"] != run["status"]:
        ch["status"] = st["state"]
    for key in ("exit_code", "started_at", "ended_at"):
        if st.get(key) is not None and st.get(key) != run.get(key):
            ch[key] = st[key]
    if st.get("source") == "none" and run["status"] in ("queued", "running"):
        rdir = hpc.get("remote_run_dir")
        remote_status = json.loads(ssh.read_file(posixpath.join(rdir, "status.json")) or "null") if rdir else None
        if remote_status and remote_status.get("state") in RUN_TERMINAL:
            ch["status"] = remote_status["state"]
            if remote_status.get("exit_code") is not None:
                ch["exit_code"] = remote_status["exit_code"]
        else:
            out["needs_attention"].append({
                "run": run["id"], "why": st.get("note") or "Slurm has no record of this job",
                "do": "the status was left unchanged. Re-run `research hpc sync` later; if accounting "
                      "stays silent, check the run directory on the cluster."})
            return
    if ch:
        slurm = dict(run.get("slurm") or {})
        for k in ("slurm_nodelist", "slurm_state", "slurm_partition"):
            if st.get(k):
                slurm[k.replace("slurm_", "")] = st[k]
        slurm["hpc"] = {**hpc, "last_sync": now_iso()}
        ch["slurm"] = slurm
        with p.tx():
            _update_run_row(p, run["id"], ch)
            if "status" in ch:
                p.event("run", run["id"], ch["status"],
                        f"{run['id']} {ch['status']} on {cluster['name']} (slurm job {job}, "
                        f"via {st.get('source')})", {"author_type": "system", "author_name": None})
        write_run_json(p, run["id"])
        out["updated"].append({"run": run["id"], "status": ch.get("status", run["status"]),
                               "slurm_job_id": job})
        if ch.get("status") in RUN_TERMINAL:
            from ..runs import _after_run_finished
            _after_run_finished(p, run["id"])
    fresh = get_run(p, run["id"])
    if collect_results and fresh["status"] in RUN_TERMINAL and _hpc_state(fresh).get("state") != "collected":
        try:
            res = collect(p, run["id"], cluster_name=cluster["name"])
            out["collected"].append({"run": run["id"], "metrics_added": res.get("metrics_added", 0),
                                     "artifacts": len(res.get("artifacts") or [])})
        except ResearchError as e:
            out["needs_attention"].append({"run": run["id"], "why": f"results could not be collected: {e}",
                                           "do": f"run `research hpc collect {run['id']}` when the cluster "
                                                 "is reachable; the run status is already recorded."})


def _pending_analysis(p: Project) -> List[Dict[str, Any]]:
    """Experiments whose runs finished and now need a human/agent interpretation.

    Exposed through the existing task/synthesis machinery — no agent is dispatched. Research OS cannot
    run an agent while the laptop is off, and does not pretend otherwise.
    """
    from ..services import experiments_needing_synthesis
    out = []
    for e in experiments_needing_synthesis(p):
        tasks = p.q("SELECT id, status FROM tasks WHERE related_experiment_id=? AND status != 'done' "
                    "ORDER BY id", (e["id"],))
        out.append({"experiment": e["id"], "title": e["title"],
                    "unsynthesized_runs": e["state"]["unsynthesized"],
                    "open_tasks": [t["id"] for t in tasks],
                    "do": f"research experiment synthesize {e['id']}"})
    return out


def cancel(p: Project, run_id: str, *, cluster_name: Optional[str] = None) -> Dict[str, Any]:
    """Cancel a remote job with scancel, then record the run as cancelled."""
    from ..runs import get_run, update_run
    run = get_run(p, run_id)
    if run["status"] in RUN_TERMINAL:
        raise ResearchError(f"{run['id']} is already {run['status']}")
    hpc = _hpc_state(run)
    job = run.get("slurm_job_id") or hpc.get("slurm_job_id")
    if not job:
        raise ResearchError(f"{run['id']} has no slurm job id recorded",
                            hint="Run `research hpc sync` first; it can recover a job id from the "
                                 "remote receipt.")
    cluster = C.get(p, cluster_name or hpc.get("cluster"))
    ssh = connect(cluster)
    r = ssh.run(["scancel", str(job)], timeout=60.0)
    if not r.ok:
        raise ResearchError(f"scancel {job} failed on {cluster['name']}: {r.output.strip()[-300:]}",
                            hint="The run record was left unchanged.")
    update_run(p, run["id"], status="cancelled", notes=f"scancel {job} on {cluster['name']}")
    _set_hpc_state(p, run["id"], state="cancelled")
    return {"run": run["id"], "slurm_job_id": str(job), "cluster": cluster["name"], "status": "cancelled"}


# =============================================================================== inspection

def inspect(p: Project, *, cluster_name: Optional[str] = None, project_slug: Optional[str] = None,
            deep: bool = False) -> Dict[str, Any]:
    """What is actually on the cluster, and how it compares with the local records. Read-only."""
    cluster = C.get(p, cluster_name)
    paths = C.paths(p, cluster)
    if project_slug:
        paths = {**paths, "project": posixpath.join(paths["projects"], project_slug)}
        for sub in ("code", "runs", "results", "metadata"):
            paths[sub] = posixpath.join(paths["project"], sub)
    ssh = connect(cluster)
    out: Dict[str, Any] = {"cluster": cluster["name"], "paths": paths,
                           "datasets": [], "deployments": [], "remote_runs": [], "discrepancies": []}

    if paths.get("shared_data"):
        try:
            out["datasets"] = [{k: d.get(k) for k in ("id", "status", "version", "has_manifest")}
                               for d in D.list_datasets(p, cluster, ssh=ssh)["datasets"]]
        except ResearchError as e:
            out["datasets_error"] = str(e)

    for exp in sorted(ssh.listdir(paths["code"])):
        for run in sorted(ssh.listdir(posixpath.join(paths["code"], exp))):
            token = (ssh.read_file(posixpath.join(paths["code"], exp, run, ".bundle-token")) or "").strip()
            out["deployments"].append({"experiment": exp, "run": run, "token": token or None,
                                       "path": posixpath.join(paths["code"], exp, run)})
    for run in sorted(ssh.listdir(paths["runs"])):
        rdir = posixpath.join(paths["runs"], run)
        entry: Dict[str, Any] = {"run": run, "path": rdir}
        st = ssh.read_file(posixpath.join(rdir, "status.json"))
        if st:
            try:
                entry["status"] = json.loads(st)
            except ValueError:
                entry["status"] = None
        rec = ssh.read_file(posixpath.join(rdir, RECEIPT))
        if rec:
            try:
                entry["receipt"] = json.loads(rec)
            except ValueError:
                pass
        out["remote_runs"].append(entry)

    # local vs remote
    local = {r["id"]: r for r in p.q("SELECT id, status, slurm_job_id, backend FROM runs")}
    remote_ids = {x["run"] for x in out["remote_runs"]} | {d["run"] for d in out["deployments"]}
    for rid in sorted(remote_ids - set(local)):
        out["discrepancies"].append({"run": rid, "issue": "on the cluster but not in this project",
                                     "do": "it may belong to another project sharing this slug, or to a "
                                           "project whose records were lost; nothing was changed"})
    for rid, row in sorted(local.items()):
        if row["backend"] != "slurm" or row["status"] in RUN_TERMINAL:
            continue
        if rid not in remote_ids:
            out["discrepancies"].append({"run": rid, "issue": f"recorded as {row['status']} here but has no "
                                                              "remote run directory",
                                         "do": "run `research hpc sync`"})
    return out
