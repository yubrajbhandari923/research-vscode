"""Project-level sandbox policy: profiles, scope precedence, gating and provenance. The mechanics are in `isolation.py`.

Profiles live in `sandbox.profiles` (built-ins are in DEFAULT_CONFIG; machine and project files override them, like any setting).
The profile for a job is the first of, highest precedence first:

    run (--sandbox) > task > experiment > cluster > project default (only while sandbox.enabled) > native

`sandbox.assign.tasks.T-001: restricted`, `sandbox.assign.experiments.EXP-001: gpu-research`, and
`hpc.clusters.<name>.sandbox: restricted` assign profiles at the lower scopes.

Once a restrictive profile applies it is mandatory: an agent cannot choose `native` over it, and a human must give a reason,
which is written to the audit log and to the run's provenance.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import isolation
from .util import PolicyBlocked, ResearchError, now_iso


def cfg(p) -> Dict[str, Any]:
    return p.config.get("sandbox") or {}


def profile_names(p) -> List[str]:
    return sorted((cfg(p).get("profiles") or {}).keys())


def profile(p, name: str) -> Dict[str, Any]:
    profs = cfg(p).get("profiles") or {}
    if name not in profs:
        raise ResearchError(f"Unknown sandbox profile {name!r}", hint="Known: " + ", ".join(sorted(profs)) + ". `research sandbox list`")
    try:
        return isolation.normalize(profs[name])
    except isolation.IsolationError as e:
        raise ResearchError(f"Sandbox profile {name!r}: {e}")


def _is_agent(p) -> bool:
    return (p.author or {}).get("author_type") == "agent"


def resolve(p, *, experiment: Optional[str] = None, task: Optional[str] = None, cluster: Optional[str] = None,
            run_profile: Optional[str] = None, datasets: Optional[List[str]] = None,
            override_reason: Optional[str] = None) -> Dict[str, Any]:
    c = cfg(p)
    assign = c.get("assign") or {}
    chain: List[Dict[str, Any]] = []
    if c.get("enabled"):
        chain.append({"scope": "project", "profile": c.get("default_profile") or "native"})
    if cluster:
        cl = ((p.config.get("hpc") or {}).get("clusters") or {}).get(cluster) or {}
        if cl.get("sandbox"):
            chain.append({"scope": "cluster", "profile": cl["sandbox"]})
    if experiment and (assign.get("experiments") or {}).get(experiment):
        chain.append({"scope": "experiment", "profile": assign["experiments"][experiment]})
    if task and (assign.get("tasks") or {}).get(task):
        chain.append({"scope": "task", "profile": assign["tasks"][task]})
    inherited = chain[-1]["profile"] if chain else "native"
    chosen, scope = inherited, (chain[-1]["scope"] if chain else "default")
    overridden = None
    if run_profile:
        weakening = isolation.normalize(profile(p, inherited))["runtime"] != "native" and \
            isolation.normalize(profile(p, run_profile))["runtime"] == "native"
        if weakening:
            if _is_agent(p):
                raise PolicyBlocked(f"An agent cannot run natively when the {scope} policy requires {inherited!r}",
                                    hint="Ask the human to override it with a stated reason.")
            if not override_reason:
                raise PolicyBlocked(f"This job is restricted by the {scope} policy ({inherited}); running natively weakens it",
                                    hint="Give --sandbox-override-reason to proceed; it is recorded.")
            overridden = {"from": inherited, "to": run_profile, "reason": override_reason, "by": (p.author or {}).get("author_name")}
        chosen, scope = run_profile, "run"
        chain.append({"scope": "run", "profile": run_profile})
    pol = profile(p, chosen)
    if datasets:
        pol = isolation.normalize({**pol, "filesystem": {**pol["filesystem"], "datasets": list(pol["filesystem"]["datasets"]) + list(datasets)}})
    return {"profile": chosen, "scope": scope, "policy": pol, "chain": chain, "overridden": overridden}


def context(p, run_dir: str, outputs_dir: str) -> Dict[str, Any]:
    return {"source_root": p.root, "run_dir": run_dir, "outputs_dir": outputs_dir, "ledger_root": p.ledger_root,
            "approved_roots": list(cfg(p).get("approved_roots") or [])}


def gate(p, res: Dict[str, Any], *, probe: bool = True) -> Dict[str, Any]:
    """Refuse (PolicyBlocked) before any run is created when the policy cannot be enforced. Returns the preflight report."""
    import tempfile
    scratch = tempfile.mkdtemp(prefix="research-preview-")
    try:
        pf = isolation.preflight(res["policy"], context(p, scratch, scratch), run_probe=probe and res["policy"]["runtime"] != "native"
                                 and res["policy"]["enforcement"].get("verify_mounts", True))
    finally:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)
    if not pf["allowed"]:
        raise PolicyBlocked(f"Sandbox profile {res['profile']!r} cannot be enforced: {pf['refusal']}",
                            hint="`research sandbox doctor` shows what is available. Choose a different profile or runtime; "
                                 "the job is never run unrestricted instead.")
    return pf


def attach(p, run_id: str, res: Dict[str, Any], pf: Dict[str, Any]) -> Dict[str, Any]:
    """Write the run's sandbox request (read by the runner) and record the effective policy in the run's provenance."""
    from .runs import _update_run_row
    run = p.q1("SELECT run_dir, env FROM runs WHERE id=?", (run_id,))
    run_dir = p.abspath(run["run_dir"])
    outputs = os.path.join(run_dir, "outputs")
    os.makedirs(outputs, exist_ok=True)
    ctx = context(p, run_dir, outputs)
    pol = res["policy"]
    request = {"profile": res["profile"], "scope": res["scope"], "policy": pol, "context": ctx, "created_at": now_iso()}
    with open(os.path.join(run_dir, "sandbox.json"), "w", encoding="utf-8") as f:
        json.dump(request, f, indent=2, sort_keys=True)
    env = json.loads(run["env"] or "{}")
    env["sandbox"] = {"profile": res["profile"], "scope": res["scope"], "chain": res["chain"], "overridden": res["overridden"],
                      "runtime": pf.get("runtime"), "runtime_info": pf.get("runtime_info"), "properties": pf.get("properties"),
                      "image": isolation.image_identity(pol.get("image")), "network": pol["network"]["mode"], "gpu": pol["gpu"],
                      "mounts": [m.as_dict() for m in isolation.plan(pol, ctx)] if pol["runtime"] != "native" else [],
                      "policy": pol}
    _update_run_row(p, run_id, {"env": env})
    if res["overridden"]:
        o = res["overridden"]
        p.event("run", run_id, "sandbox_override", f"{run_id}: sandbox {o['from']} → {o['to']} by {o['by']}: {o['reason']}")
    return env["sandbox"]


# ------------------------------------------------------------------------------------------------ reports
def summary(p, name: str) -> Dict[str, Any]:
    pol = profile(p, name)
    return {"name": name, "runtime": pol["runtime"], "image": pol.get("image"), "network": pol["network"]["mode"], "gpu": pol["gpu"],
            "source": pol["filesystem"]["source"], "datasets": len(pol["filesystem"]["datasets"]),
            "requests": isolation.requested_properties(pol)}


def doctor(p=None) -> Dict[str, Any]:
    return {"runtimes": [rt.info() for rt in isolation.RUNTIMES.values()],
            "note": "`available` means the tool exists; only `sandbox verify` shows which restrictions actually hold."}


def verify(p, name: str) -> Dict[str, Any]:
    pol = profile(p, name)
    if pol["runtime"] == "native":
        return {"profile": name, "runtime": "native", "allowed": True, "properties": [],
                "notes": ["native requests no restrictions; there is nothing to verify."]}
    pf = isolation.preflight(pol, None, run_probe=True)
    pf["profile"] = name
    return pf


def preview(p, experiment: Optional[str] = None, task: Optional[str] = None, cluster: Optional[str] = None,
            profile_name: Optional[str] = None, datasets: Optional[List[str]] = None) -> Dict[str, Any]:
    """What a job would get, and whether it would be refused. Runs no job and no probe."""
    res = resolve(p, experiment=experiment, task=task, cluster=cluster, run_profile=profile_name, datasets=datasets,
                  override_reason="preview" if not _is_agent(p) else None)
    pol = res["policy"]
    import tempfile, shutil
    scratch = tempfile.mkdtemp(prefix="research-preview-")
    try:
        pf = isolation.preflight(pol, context(p, scratch, scratch), run_probe=False)
        for m in pf["mounts"]:
            for k in ("host",):
                m[k] = m[k].replace(scratch, "<run directory>")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"profile": res["profile"], "scope": res["scope"], "chain": res["chain"], "policy": pol, "preflight": pf,
            "would_be_refused": not pf["allowed"], "agent_sandbox": agent_boundary(p)}


def agent_boundary(p) -> Dict[str, Any]:
    """Agents are NOT sandboxed by Research OS. This states the boundary honestly so it is never mistaken for enforcement."""
    mode = (cfg(p).get("agent") or {}).get("mode", "none")
    return {"mode": mode, "enforced": False,
            "note": "Job isolation does not restrict the agent's own shell or SSH session. 'advisory' only adds written boundaries "
                    "to the dispatch brief; nothing stops an agent from ignoring them."}
