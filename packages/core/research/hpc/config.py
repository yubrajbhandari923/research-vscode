"""Cluster profiles: reusable cluster facts (SSH alias, approved storage roots, Slurm defaults) kept apart
from the project's own settings (project slug, requested resources).

Simple configuration layering, not a configuration framework:

    DEFAULTS  <-  config.yaml hpc.clusters.<name>  <-  per-command flags

Authentication is never configured here. OpenSSH owns it: aliases, jump hosts, keys and agents all come from
`~/.ssh/config`. Research OS stores no passwords, private keys or tokens.
"""
from __future__ import annotations

import posixpath
from typing import Any, Dict, List, Optional

from ..store import Project, _deep_merge
from ..util import ResearchError

CLUSTER_DEFAULTS: Dict[str, Any] = {
    "transport": "ssh",                 # ssh | local (local = Slurm on this machine, the existing backend)
    "ssh_host": None,                   # an alias from ~/.ssh/config, e.g. "plp-drcc-login2"
    "paths": {
        "workspace_root": None,         # everything Research OS writes lives under here
        "shared_data": None,            # read-mostly datasets shared between projects
        "projects": None,               # defaults to <workspace_root>/projects
        "scratch": None,                # optional: temporary job outputs on a faster filesystem
    },
    "transfer": {"method": "rsync"},    # rsync | scp (scp is the fallback when rsync is absent)
    "datasets": {
        "reuse_existing": True,
        "verify_before_reuse": True,
        "auto_delete": False,           # Research OS never deletes remote data; this stays false
        "require_download_approval": True,
    },
    "execution": {
        "use_slurm": True,
        "auto_submit": False,           # submission needs an explicit command or --yes
        "preserve_code": True,          # a submitted code snapshot is never overwritten
        "preserve_results": True,
    },
    "slurm": {"partition": None, "account": None, "time": "04:00:00", "cpus": 4, "mem": "16G",
              "gpus": None, "extra_args": [], "setup": []},
    "project": {"slug": None},          # remote directory name; defaults to a slug of the project name
    "limits": {"max_cpus": 64, "max_gpus": 8, "max_time_hours": 72, "max_mem_gb": 512},
    "python": None,                     # remote interpreter for the runner; None → `python3`
    # Remote commands run through a login shell by default: on many clusters a plain `ssh host sbatch`
    # misses the profile scripts that put Slurm on PATH, so it fails where an interactive ssh works.
    "login_shell": True,
    "remote_shell": "bash",
}

SAFE_PATH_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/~")


def clusters(p: Project) -> Dict[str, Dict[str, Any]]:
    """Every configured cluster, with defaults filled in."""
    raw = (p.config.get("hpc") or {}).get("clusters") or {}
    out = {}
    for name, cfg in sorted(raw.items()):
        out[name] = _deep_merge(CLUSTER_DEFAULTS, cfg or {})
        out[name]["name"] = name
    return out


def names(p: Project) -> List[str]:
    return sorted(clusters(p).keys())


def default_name(p: Project) -> Optional[str]:
    cfg = p.config.get("hpc") or {}
    d = cfg.get("default_cluster")
    if d:
        return d
    avail = names(p)
    return avail[0] if len(avail) == 1 else None


def get(p: Project, name: Optional[str] = None) -> Dict[str, Any]:
    """Resolve one cluster profile. Requires an explicit choice when several are configured."""
    all_ = clusters(p)
    if not all_:
        raise ResearchError(
            "No HPC cluster is configured for this project.",
            hint="Add one under `hpc.clusters` in .research/config.yaml, then run `research hpc doctor`. "
                 "See `research hpc config --example`. HPC support is opt-in; nothing else needs it.")
    chosen = name or default_name(p)
    if not chosen:
        raise ResearchError(f"Several clusters are configured ({', '.join(sorted(all_))}) and no default is set.",
                            hint="Pass --cluster NAME, or set `hpc.default_cluster` in .research/config.yaml.")
    if chosen not in all_:
        raise ResearchError(f"Unknown cluster {chosen!r} (configured: {', '.join(sorted(all_)) or 'none'})",
                            hint="Check `hpc.clusters` in .research/config.yaml.")
    return all_[chosen]


def slug(p: Project, cluster: Dict[str, Any]) -> str:
    """The remote project directory name."""
    from ..util import slugify
    return (cluster.get("project") or {}).get("slug") or slugify(p.name)


def check_remote_path(path: str, what: str) -> str:
    """Validate a configured remote path. Absolute, no shell metacharacters, no traversal."""
    if not path:
        raise ResearchError(f"{what} is not configured",
                            hint="Set the approved absolute path under `hpc.clusters.<name>.paths` "
                                 "in .research/config.yaml. Research OS will not guess a storage location.")
    if not (path.startswith("/") or path.startswith("~/")):
        raise ResearchError(f"{what} must be an absolute remote path (or start with ~/), got {path!r}")
    bad = sorted(set(path) - SAFE_PATH_CHARS)
    if bad:
        raise ResearchError(f"{what} contains characters that are unsafe in a remote path: {''.join(bad)!r}")
    if ".." in path.split("/"):
        raise ResearchError(f"{what} must not contain '..': {path!r}")
    return path.rstrip("/") or "/"


def paths(p: Project, cluster: Dict[str, Any]) -> Dict[str, str]:
    """The validated remote directory layout for this project on this cluster.

    Only explicitly approved roots are used: `$HOME` is never assumed to be a suitable place for data.
    """
    cp = cluster.get("paths") or {}
    workspace = check_remote_path(cp.get("workspace_root") or "", "hpc.clusters.<name>.paths.workspace_root")
    projects = cp.get("projects") or posixpath.join(workspace, "projects")
    projects = check_remote_path(projects, "hpc.clusters.<name>.paths.projects")
    out = {
        "workspace_root": workspace,
        "projects": projects,
        "project": posixpath.join(projects, slug(p, cluster)),
    }
    for key, label in (("shared_data", "paths.shared_data"), ("scratch", "paths.scratch")):
        if cp.get(key):
            out[key] = check_remote_path(cp[key], f"hpc.clusters.<name>.{label}")
    for sub in ("code", "runs", "results", "metadata"):
        out[sub] = posixpath.join(out["project"], sub)
    return out


def inside(root: str, candidate: str) -> bool:
    """True when `candidate` is `root` or below it. Guards every remote write."""
    r = posixpath.normpath(root).rstrip("/") or "/"
    c = posixpath.normpath(candidate)
    return c == r or c.startswith(r + "/")


def check_inside(allowed: List[str], candidate: str, what: str = "path") -> str:
    """Refuse any remote path outside the approved roots."""
    candidate = check_remote_path(candidate, what)
    if not any(inside(a, candidate) for a in allowed if a):
        raise ResearchError(f"Refusing to touch {candidate!r}: it is outside the configured HPC paths "
                            f"({', '.join(a for a in allowed if a)})",
                            hint="Research OS only reads and writes inside the roots listed under "
                                 "`hpc.clusters.<name>.paths`.")
    return candidate


def check_resources(cluster: Dict[str, Any], opts: Dict[str, Any]) -> Dict[str, Any]:
    """Merge requested Slurm resources with the cluster's defaults and enforce its bounds."""
    merged = _deep_merge(cluster.get("slurm") or {}, {k: v for k, v in opts.items() if v not in (None, "", [])})
    lim = cluster.get("limits") or {}
    if merged.get("cpus") and lim.get("max_cpus") and int(merged["cpus"]) > int(lim["max_cpus"]):
        raise ResearchError(f"{merged['cpus']} CPUs exceeds this cluster's configured limit of {lim['max_cpus']}",
                            hint="Raise `limits.max_cpus` for this cluster if that is really intended.")
    gpus = merged.get("gpus")
    if gpus and lim.get("max_gpus"):
        try:
            n = int(str(gpus).split(":")[-1])
        except ValueError:
            n = 1
        if n > int(lim["max_gpus"]):
            raise ResearchError(f"{gpus} GPUs exceeds this cluster's configured limit of {lim['max_gpus']}",
                                hint="Raise `limits.max_gpus` for this cluster if that is really intended.")
    t = merged.get("time")
    if t and lim.get("max_time_hours"):
        hours = _hours(str(t))
        if hours is not None and hours > float(lim["max_time_hours"]):
            raise ResearchError(f"a {t} wall-time request exceeds this cluster's configured limit of "
                                f"{lim['max_time_hours']}h",
                                hint="Raise `limits.max_time_hours`, or split the job and use a checkpoint/restart "
                                     "command in the experiment metadata.")
    mem = merged.get("mem")
    if mem and lim.get("max_mem_gb"):
        gb = _gb(str(mem))
        if gb is not None and gb > float(lim["max_mem_gb"]):
            raise ResearchError(f"a {mem} memory request exceeds this cluster's configured limit of "
                                f"{lim['max_mem_gb']}G", hint="Raise `limits.max_mem_gb` if that is intended.")
    return merged


def _hours(t: str) -> Optional[float]:
    """Parse a Slurm wall-time ('4', '4:00', '04:00:00', '2-00:00:00') into hours."""
    days = 0.0
    if "-" in t:
        d, _, t = t.partition("-")
        try:
            days = float(d)
        except ValueError:
            return None
    parts = t.split(":")
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        return days * 24 + nums[0] / 60        # bare number = minutes, per sbatch
    if len(nums) == 2:
        return days * 24 + nums[0] / 60 + nums[1] / 3600
    return days * 24 + nums[0] + nums[1] / 60 + nums[2] / 3600


def _gb(mem: str) -> Optional[float]:
    m = mem.strip().upper()
    mult = {"K": 1 / (1024 * 1024), "M": 1 / 1024, "G": 1.0, "T": 1024.0}
    unit = m[-1] if m and m[-1] in mult else "M"   # sbatch's default unit is megabytes
    num = m[:-1] if m and m[-1] in mult else m
    try:
        return float(num) * mult[unit]
    except ValueError:
        return None


EXAMPLE = """# Add this to .research/config.yaml and replace the paths with your cluster's approved storage.
# Authentication stays in ~/.ssh/config: `ssh_host` is an alias you can already `ssh` to.
hpc:
  default_cluster: duke
  clusters:
    duke:
      transport: ssh
      ssh_host: plp-drcc-login2          # must already work: `ssh plp-drcc-login2`
      paths:
        workspace_root: /CHANGE/ME/research-os
        shared_data: /CHANGE/ME/shared-data
        projects: /CHANGE/ME/research-os/projects
      transfer:
        method: rsync
      execution:
        use_slurm: true
        auto_submit: false
      slurm:
        partition: null
        account: null
        time: "04:00:00"
        cpus: 4
        mem: 16G
        gpus: 1
        setup:
          - module load python/3.11
      limits:
        max_cpus: 64
        max_gpus: 8
        max_time_hours: 72
        max_mem_gb: 512
      project:
        slug: my-project
"""
