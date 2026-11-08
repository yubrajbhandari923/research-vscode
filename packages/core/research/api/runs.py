"""Runs, artifacts and metrics."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .. import runs as R
from . import op


@op(mutates=True)
def run_attach(p, experiment: str, **kw: Any) -> Dict[str, Any]:
    return R.attach_run(p, experiment, **kw)


@op(mutates=True)
def run_start(p, experiment: str, command: str, backend: str = "local", **kw: Any) -> Dict[str, Any]:
    if backend == "slurm":
        slurm = kw.pop("slurm", None) or {}
        return R.submit_run(p, experiment, command, **kw, **slurm)
    return R.exec_run(p, experiment, command, detach=True, **kw)


@op(mutates=True)
def run_cancel(p, id: str) -> Dict[str, Any]:
    return R.cancel_run(p, id)


@op(sync=False)
def run_refresh(p, id: Optional[str] = None) -> Any:
    if id:
        return R.reconcile_run(p, id, force=True)
    R.sync_runs(p, slurm_min_interval=0)
    return {"ok": True}


@op(mutates=True)
def register_artifact(p, path: str, **kw: Any) -> Dict[str, Any]:
    return R.register_artifact(p, path, **kw)


@op(mutates=True)
def log_metric(p, name: str, value: Any, **kw: Any) -> Dict[str, Any]:
    return R.log_metric(p, name, value, **kw)
