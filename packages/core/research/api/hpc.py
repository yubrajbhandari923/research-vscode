"""HPC, environment checks and sandbox preview."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..schema import normalize_id
from ..store import Project
from ..util import ResearchError
from . import op


@op(mcp="hpc_doctor")
def hpc_doctor(p, cluster: Optional[str] = None) -> Dict[str, Any]:
    from ..hpc import remote as HR
    return HR.doctor(p, cluster)


@op()
def hpc_status(p) -> Dict[str, Any]:
    """HPC overview for the Run view: configured clusters plus every run with remote state."""
    from ..hpc import config as HC
    runs = []
    for r in p.q("SELECT id, experiment_id, status, slurm_job_id, backend, slurm FROM runs "
                 "WHERE backend IN ('slurm','hpc-bundle') ORDER BY id DESC LIMIT 200"):
        hpc = ((json.loads(r["slurm"] or "{}") or {}).get("hpc")) or {}
        runs.append({"id": r["id"], "experiment_id": r["experiment_id"], "status": r["status"],
                     "slurm_job_id": r["slurm_job_id"], "backend": r["backend"],
                     "cluster": hpc.get("cluster"), "hpc_state": hpc.get("state"),
                     "remote_run_dir": hpc.get("remote_run_dir"),
                     "collected_at": hpc.get("collected_at")})
    return {"clusters": HC.names(p), "default_cluster": HC.default_name(p), "runs": runs, "enabled": bool(HC.names(p))}


def _exp_project(p: Project, experiment: str):
    """(Project rooted at the experiment's worktree, whether it is a new handle to close): a bundle must ship the experiment's code."""
    from .. import workspace as W
    exp = normalize_id(experiment, "EXP")
    for rec in W._fold(W._records(p)).values():
        if rec["status"] == "active" and rec.get("experiment") == exp:
            s = W.show(p, rec["id"])
            if s["present"]:
                return Project(s["path"], author=p.author, sync=False), True
    return p, False


@op(mutates=True)
def hpc_init(p, cluster: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
    """Create the remote project directories (the preview is the default)."""
    from ..hpc import remote as HR
    return HR.init_workspace(p, cluster, dry_run=dry_run)


@op(mutates=True)
def hpc_submit(p, experiment: str, command: str, cluster: Optional[str] = None, dry_run: bool = True,
               approve: bool = False, resources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Submit one run of EXPERIMENT's worktree code to the cluster. Preview first; `approve` is the explicit go-ahead."""
    from ..hpc import remote as HR
    if not command or not command.strip():
        raise ResearchError("A command to run is required")
    x, owned = _exp_project(p, experiment)
    try:
        return HR.submit(x, experiment, cluster_name=cluster, command=command.strip(), dry_run=dry_run,
                         approve=approve and not dry_run, resources=resources or {})
    finally:
        if owned:
            x.close()


@op(mutates=True, mcp="hpc_sync")
def hpc_sync(p, cluster: Optional[str] = None) -> Dict[str, Any]:
    from ..hpc import remote as HR
    return HR.sync(p, cluster_name=cluster)


@op(mutates=True, mcp="hpc_bundle")
def hpc_bundle(p, experiment: str, command: Optional[str] = None) -> Dict[str, Any]:
    from ..hpc import bundle as HB
    return {k: v for k, v in HB.build(p, experiment, command=command).items() if k != "manifest"}


@op(needs_init=False, needs_project=False)
def hpc_discover(s) -> Dict[str, Any]:
    """SSH aliases from ~/.ssh/config. Reads a local file; contacts nothing."""
    from .. import initproject
    return {"hosts": initproject.ssh_hosts()}


@op(needs_init=False, needs_project=False)
def hpc_test(s, ssh_host: str, workspace_root: str = "", python: str = "", required_packages: Any = None, setup: Any = None,
             partition: str = "", compute: bool = False) -> Dict[str, Any]:
    """Explicit, read-only connection test (the existing `hpc doctor` checks) before any project exists. With `compute`, also one
    one-minute srun to prove the job environment on a compute node."""
    from types import SimpleNamespace
    from ..hpc import remote as HR
    if not ssh_host or any(c.isspace() for c in ssh_host) or ssh_host.startswith("-"):
        raise ResearchError("Invalid SSH host alias")
    lines = lambda v: [x.strip() for x in (v.splitlines() if isinstance(v, str) else (v or [])) if str(x).strip()]
    cl: Dict[str, Any] = {"ssh_host": ssh_host, "paths": {"workspace_root": workspace_root or "~/research-os-probe"}}
    if python:
        cl["python"] = python
    if required_packages:
        cl["required_packages"] = [x.strip() for x in (required_packages.split(",") if isinstance(required_packages, str) else required_packages) if str(x).strip()]
    sl = {k: v for k, v in (("setup", lines(setup)), ("partition", partition or None)) if v}
    if sl:
        cl["slurm"] = sl
    fake = SimpleNamespace(config={"hpc": {"clusters": {"probe": cl}}}, name="probe", root=s.root)
    return HR.doctor(fake, "probe", compute=compute)


@op(sync=False)
def env_check(p, packages: Optional[List[str]] = None, python: Optional[str] = None) -> Dict[str, Any]:
    from .. import envcheck
    if python:                                   # test a candidate before it is saved
        saved = p.config.get("environment")
        p.config["environment"] = {"python": python, "packages": packages or []}
        try:
            return envcheck.check(p, packages)
        finally:
            p.config["environment"] = saved
    return envcheck.check(p, packages)


@op(needs_project=False)
def env_probe(s, python: str, packages: Optional[List[str]] = None) -> Dict[str, Any]:
    """Test an existing interpreter or venv before any project exists (the wizard's *Use an existing environment*)."""
    from types import SimpleNamespace
    from .. import envcheck
    fake = SimpleNamespace(config={"environment": {"python": python, "packages": packages or []}}, root=s.root, rdir=s.root)
    return envcheck.check(fake, packages)


@op(sync=False)
def sandbox_preview(p, experiment: Optional[str] = None, task: Optional[str] = None, profile: Optional[str] = None,
                    datasets: Optional[List[str]] = None) -> Dict[str, Any]:
    from .. import sandbox as SB
    return SB.preview(p, experiment=experiment, task=task, profile_name=profile, datasets=datasets)
