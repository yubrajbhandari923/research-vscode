"""Execution isolation: policy, mount planning and pluggable runtimes.

Pure standard library and independent of the project database, because the job runner (`runner.py`) imports it on
compute nodes. Nothing here claims more than it can show:

    requested     the policy asks for the restriction
    enforceable   the runtime is present and is able to apply it
    verified      a canary run under the SAME runtime and mount layout observed the restriction holding
    unavailable   the runtime cannot apply it here

A policy that requests restrictions is *strict*: if any requested restriction is not enforceable the job is refused. There is no
fallback to native execution. `native` is the explicit, unrestricted choice; it enforces nothing and says so.

Runtimes (register more with `register_runtime`):
    native     no isolation (the pre-existing behaviour)
    userns     unprivileged Linux user + mount + pid (+ net) namespaces with pivot_root. Local workstation / login node, when the
               kernel allows unprivileged user namespaces. Not a hardened security boundary against a malicious program that also
               exploits the kernel; it prevents accidental reads/writes outside the declared mounts.
    apptainer  Apptainer/Singularity container. Mount isolation depends on the site's configuration, which is why it is probed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

PROPERTIES = ("filesystem.outside_hidden", "filesystem.source_readonly", "filesystem.datasets_readonly",
              "filesystem.outputs_scoped", "filesystem.home_hidden", "filesystem.ledger_hidden",
              "network.disabled", "env.clean", "pid.isolated", "gpu.access")

PROFILE_SHAPE: Dict[str, Any] = {
    "runtime": "native", "image": None,
    "filesystem": {"source": "read_only", "datasets": [], "read_only": [], "read_write": [], "outputs": "read_write",
                   "scratch": "temporary", "home": "hidden", "ledger": "hidden"},
    "network": {"mode": "enabled"}, "gpu": False,
    "env": {"allow": [], "set": {}},
    "enforcement": {"fail_if_unavailable": True, "verify_mounts": True},
}
# Credential locations that are never mountable, whatever the policy says.
SECRET_DIRS = (".ssh", ".aws", ".gnupg", ".config/gcloud", ".kube", ".docker", ".netrc", ".azure", ".config/gh", ".claude",
               ".config/research-os")
SYSTEM_RO = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/opt/conda", "/etc/alternatives", "/etc/ssl", "/etc/ld.so.cache",
             "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf", "/etc/localtime",
             "/etc/resolv.conf", "/etc/ca-certificates", "/etc/pki")


class IsolationError(Exception):
    """A requested restriction cannot be applied or a policy is unsafe. Always means: do not run."""

    def __init__(self, message: str, hint: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.hint, self.details = hint, details or {}


def _merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def normalize(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Fill defaults and validate shape. Raises IsolationError with every problem found."""
    pol = _merge(PROFILE_SHAPE, profile or {})
    errs: List[str] = []
    fs = pol["filesystem"]
    if fs["source"] not in ("read_only", "read_write", "hidden"):
        errs.append("filesystem.source must be read_only, read_write or hidden")
    if fs["outputs"] != "read_write":
        errs.append("filesystem.outputs must be read_write (a run needs somewhere to write)")
    if fs["scratch"] not in ("temporary", "hidden"):
        errs.append("filesystem.scratch must be temporary or hidden")
    if fs["home"] != "hidden":
        errs.append("filesystem.home must be hidden: exposing a home directory is not supported")
    if fs["ledger"] != "hidden":
        errs.append("filesystem.ledger must be hidden: jobs may not touch the research ledger")
    if pol["network"].get("mode") not in ("enabled", "disabled"):
        errs.append("network.mode must be enabled or disabled")
    if pol["runtime"] != "native" and pol["runtime"] != "auto" and pol["runtime"] not in RUNTIMES:
        errs.append(f"unknown runtime {pol['runtime']!r} (known: {', '.join(sorted(RUNTIMES))}, auto)")
    for key in ("datasets", "read_only", "read_write"):
        if not isinstance(fs.get(key), list):
            errs.append(f"filesystem.{key} must be a list")
    if errs:
        raise IsolationError("Invalid sandbox profile: " + "; ".join(errs))
    return pol


def requested_properties(pol: Dict[str, Any]) -> List[str]:
    if pol["runtime"] == "native":
        return []
    fs = pol["filesystem"]
    want = ["filesystem.outside_hidden", "filesystem.outputs_scoped", "filesystem.home_hidden", "filesystem.ledger_hidden",
            "env.clean", "pid.isolated"]
    if fs["source"] == "read_only":
        want.append("filesystem.source_readonly")
    if fs["datasets"]:
        want.append("filesystem.datasets_readonly")
    if pol["network"]["mode"] == "disabled":
        want.append("network.disabled")
    if pol["gpu"]:
        want.append("gpu.access")
    return want


# ------------------------------------------------------------------------------------------------ mount planning
class Mount:
    def __init__(self, host: str, target: str, mode: str, why: str):
        self.host, self.target, self.mode, self.why = host, target, mode, why   # mode: ro | rw

    def as_dict(self) -> Dict[str, str]:
        return {"host": self.host, "target": self.target, "mode": self.mode, "why": self.why}


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def safe_host_path(raw: Any, label: str, forbidden: List[str], approved_roots: List[str]) -> str:
    """Resolve a user-supplied host path and refuse anything unsafe. Returns the resolved real path."""
    path = raw["path"] if isinstance(raw, dict) else raw
    if not isinstance(path, str) or not path or "\x00" in path:
        raise IsolationError(f"{label}: empty or invalid path")
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        raise IsolationError(f"{label}: {path!r} must be an absolute path", hint="Relative paths are ambiguous inside a sandbox.")
    if ".." in p.replace("\\", "/").split("/"):
        raise IsolationError(f"{label}: {path!r} contains '..'")
    real = os.path.realpath(p)
    if not os.path.exists(real):
        raise IsolationError(f"{label}: {path!r} does not exist")
    home = os.path.realpath(os.path.expanduser("~"))
    if real == os.sep or real == home:
        raise IsolationError(f"{label}: {real!r} is too broad to mount", hint="Mount the specific directory the job needs.")
    for s in SECRET_DIRS:
        sp = os.path.join(home, s)
        if _is_under(real, sp) or _is_under(sp, real):
            raise IsolationError(f"{label}: {real!r} overlaps credentials ({sp}); credentials are never mounted")
    for f in forbidden:
        if _is_under(real, f) or _is_under(f, real):
            raise IsolationError(f"{label}: {real!r} overlaps a protected path ({f})",
                                 hint="The research ledger and the run's own control files are never exposed.")
    if approved_roots and not any(_is_under(real, os.path.realpath(r)) for r in approved_roots):
        raise IsolationError(f"{label}: {real!r} is outside sandbox.approved_roots",
                             hint="Add its parent directory to sandbox.approved_roots if it is intended.")
    return real


def plan(pol: Dict[str, Any], ctx: Dict[str, Any]) -> List[Mount]:
    """The complete, resolved mount list for a policy. `ctx`: source_root, run_dir, outputs_dir, ledger_root, approved_roots."""
    fs = pol["filesystem"]
    forbidden = [os.path.realpath(ctx["ledger_root"])] if ctx.get("ledger_root") else []
    roots = ctx.get("approved_roots") or []
    mounts: List[Mount] = []
    if fs["source"] != "hidden":
        mounts.append(Mount(os.path.realpath(ctx["source_root"]), "/workspace", "ro" if fs["source"] == "read_only" else "rw",
                            "source code"))
    used = {"/workspace", "/outputs", "/run", "/tmp"}
    for d in fs["datasets"]:
        host = safe_host_path(d, "dataset", [f for f in forbidden], roots)
        name = (d.get("name") if isinstance(d, dict) else None) or os.path.basename(host.rstrip(os.sep)) or "data"
        target, i = f"/data/{name}", 2
        while target in used:
            target, i = f"/data/{name}-{i}", i + 1
        used.add(target)
        mounts.append(Mount(host, target, "ro", "dataset (read-only)"))
    for key, mode in (("read_only", "ro"), ("read_write", "rw")):
        for d in fs[key]:
            host = safe_host_path(d, f"filesystem.{key}", forbidden, roots)
            target = (d.get("target") if isinstance(d, dict) else None) or host
            if not target.startswith("/") or target in used or target.startswith(("/proc", "/dev", "/sys")):
                raise IsolationError(f"filesystem.{key}: bad or duplicate target {target!r}")
            used.add(target)
            mounts.append(Mount(host, target, mode, "declared " + ("read-only" if mode == "ro" else "writable") + " mount"))
    out = os.path.realpath(ctx["outputs_dir"])
    mounts.append(Mount(out, "/outputs", "rw", "this run's output directory"))
    mounts.append(Mount(os.path.realpath(ctx["run_dir"]), "/run", "rw", "run control files (logs, metrics.jsonl, status.json)"))
    return mounts


def job_env(pol: Dict[str, Any], extra: Dict[str, str]) -> Dict[str, str]:
    """Clean environment: a short allowlist, never the caller's whole environment."""
    keep = ["LANG", "LC_ALL", "TERM", "TZ", "CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "SLURM_JOB_ID", "SLURM_CPUS_PER_TASK"]
    keep += list(pol["env"].get("allow") or [])
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp"})
    env.update({str(k): str(v) for k, v in (pol["env"].get("set") or {}).items()})
    env.update(extra)
    return env


# ------------------------------------------------------------------------------------------------ runtimes
class Runtime:
    name = "base"
    capabilities: Tuple[str, ...] = ()

    def detect(self) -> Dict[str, Any]:
        raise NotImplementedError

    def argv(self, pol: Dict[str, Any], mounts: List[Mount], command: str, cwd: str, env: Dict[str, str]) -> Tuple[List[str], Dict[str, str], List[str]]:
        """(argv, env for the launcher, temp dirs to remove afterwards)."""
        raise NotImplementedError

    def info(self) -> Dict[str, Any]:
        d = self.detect()
        return {"runtime": self.name, **d, "capabilities": list(self.capabilities)}


class NativeRuntime(Runtime):
    name = "native"

    def detect(self) -> Dict[str, Any]:
        return {"available": True, "version": None, "notes": ["No isolation: the job has your full user permissions."]}

    def argv(self, pol, mounts, command, cwd, env):
        return [shutil.which("bash") or "/bin/sh", "-c", command], dict(os.environ), []


class UsernsRuntime(Runtime):
    name = "userns"
    capabilities = ("filesystem.outside_hidden", "filesystem.source_readonly", "filesystem.datasets_readonly",
                    "filesystem.outputs_scoped", "filesystem.home_hidden", "filesystem.ledger_hidden", "network.disabled",
                    "env.clean", "pid.isolated", "gpu.access")

    def detect(self) -> Dict[str, Any]:
        if not sys.platform.startswith("linux"):
            return {"available": False, "version": None, "notes": ["Linux only."]}
        tools = {t: shutil.which(t) for t in ("unshare", "pivot_root", "mount", "mktemp")}
        missing = [t for t, v in tools.items() if not v]
        if missing:
            return {"available": False, "version": None, "notes": [f"Missing tools: {', '.join(missing)} (util-linux)."]}
        try:
            r = subprocess.run(["unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork", "true"],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"available": False, "version": None, "notes": [f"unshare failed: {e}"]}
        if r.returncode != 0:
            return {"available": False, "version": None,
                    "notes": ["Unprivileged user namespaces are disabled on this system: " + (r.stderr.strip()[-200:] or "unshare failed")]}
        ver = subprocess.run(["unshare", "--version"], capture_output=True, text=True).stdout.strip()
        return {"available": True, "version": ver, "notes": ["Needs unprivileged user namespaces. The job sees itself as uid 0 inside; "
                                                             "files it creates belong to you outside."]}

    def argv(self, pol, mounts, command, cwd, env):
        root = tempfile.mkdtemp(prefix="research-sbx-")
        s = ["set -e", "mount --make-rprivate /", f"R={shlex.quote(root)}", 'mount -t tmpfs -o mode=755 tmpfs "$R"',
             'mkdir -p "$R/proc" "$R/dev" "$R/tmp" "$R/.old"']

        def bind(host: str, target: str, mode: str) -> None:
            t = f'"$R"{shlex.quote(target)}'
            if os.path.isdir(host):
                s.append(f"mkdir -p {t}")
            else:
                s.append(f"mkdir -p \"$(dirname {t})\"; : > {t}")
            s.append(f"mount --rbind {shlex.quote(host)} {t}")
            if mode == "ro":
                s.append(f"mount -o remount,ro,bind {t}")

        for sysd in SYSTEM_RO:
            if os.path.exists(sysd):
                real = os.path.realpath(sysd)
                if os.path.islink(sysd):   # merged-usr symlinks (/bin -> usr/bin): recreate the link, the target is bound anyway
                    s.append(f'ln -s {shlex.quote(os.readlink(sysd))} "$R"{shlex.quote(sysd)} 2>/dev/null || true')
                    continue
                bind(real, sysd, "ro")
        for m in mounts:
            bind(m.host, m.target, m.mode)
        # an empty tmpfs over a nested ledger keeps it invisible even when the source tree contains it
        for m in mounts:
            if m.target == "/workspace" and os.path.isdir(os.path.join(m.host, ".research")):
                s.append('mount -t tmpfs -o mode=755,size=1m tmpfs "$R/workspace/.research"')
        for dev in ("null", "zero", "random", "urandom", "tty"):
            s.append(f'[ -e /dev/{dev} ] && : > "$R/dev/{dev}" && mount --bind /dev/{dev} "$R/dev/{dev}" || true')
        if pol["gpu"]:
            for d in sorted(__import__("glob").glob("/dev/nvidia*")) + [x for x in ("/dev/dxg",) if os.path.exists(x)]:
                s.append(f': > "$R{d}" && mount --bind {shlex.quote(d)} "$R{d}" || true')
        if pol["filesystem"]["scratch"] == "temporary":
            s.append('mount -t tmpfs -o mode=1777 tmpfs "$R/tmp"')
        s += ['mount -t proc proc "$R/proc"', 'cd "$R"', "pivot_root . .old", "umount -l /.old", "rmdir /.old 2>/dev/null || true",
              f"cd {shlex.quote(cwd)} 2>/dev/null || cd /", 'exec /usr/bin/env -i "$@"']
        inner = [f"{k}={v}" for k, v in env.items()] + [shutil.which("bash") and "/bin/bash" or "/bin/sh", "-c", command]
        flags = ["--user", "--map-root-user", "--mount", "--pid", "--fork", "--ipc", "--uts"]
        if pol["network"]["mode"] == "disabled":
            flags.append("--net")
        return ["unshare", *flags, "sh", "-c", "; ".join(s), "sh", *inner], dict(os.environ), [root]


class ApptainerRuntime(Runtime):
    name = "apptainer"
    capabilities = ("filesystem.outside_hidden", "filesystem.source_readonly", "filesystem.datasets_readonly",
                    "filesystem.outputs_scoped", "filesystem.home_hidden", "filesystem.ledger_hidden", "network.disabled",
                    "env.clean", "pid.isolated", "gpu.access")

    def exe(self) -> Optional[str]:
        return shutil.which("apptainer") or shutil.which("singularity")

    def detect(self) -> Dict[str, Any]:
        exe = self.exe()
        if not exe:
            return {"available": False, "version": None, "notes": ["apptainer/singularity is not on PATH (on a cluster try `module load apptainer`)."]}
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)
        return {"available": r.returncode == 0, "version": r.stdout.strip(), "path": exe,
                "notes": ["Site configuration can add bind paths and may forbid `--network none` for unprivileged users; "
                          "only a probe shows what actually holds."]}

    def argv(self, pol, mounts, command, cwd, env):
        if not pol.get("image"):
            raise IsolationError("The apptainer runtime needs an image", hint="Set image: /path/to/image.sif in the sandbox profile.")
        img = os.path.realpath(os.path.expanduser(pol["image"]))
        if not os.path.isfile(img):
            raise IsolationError(f"Container image not found or not a file: {pol['image']}")
        tmp = tempfile.mkdtemp(prefix="research-apt-")
        a = [self.exe() or "apptainer", "exec", "--containall", "--cleanenv", "--no-home", "--no-mount", "bind-paths",
             "--pwd", cwd if cwd.startswith(("/workspace", "/outputs", "/run")) else "/workspace"]
        if pol["network"]["mode"] == "disabled":
            a += ["--net", "--network", "none"]
        if pol["gpu"]:
            a.append("--nv")
        for m in mounts:
            a += ["--bind", f"{m.host}:{m.target}:{'ro' if m.mode == 'ro' else 'rw'}"]
            if m.target == "/workspace" and os.path.isdir(os.path.join(m.host, ".research")):
                empty = os.path.join(tmp, "empty")
                os.makedirs(empty, exist_ok=True)
                a += ["--bind", f"{empty}:/workspace/.research:ro"]
        for k, v in env.items():
            a += ["--env", f"{k}={v}"]
        a += [img, "bash", "-c", command]
        return a, dict(os.environ), [tmp]


RUNTIMES: Dict[str, Runtime] = {}


def register_runtime(rt: Runtime) -> None:
    RUNTIMES[rt.name] = rt


for _rt in (NativeRuntime(), UsernsRuntime(), ApptainerRuntime()):
    register_runtime(_rt)

AUTO_ORDER = ("apptainer", "userns")


def image_identity(path: Optional[str], max_hash_bytes: int = 1 << 30) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    p = os.path.realpath(os.path.expanduser(path))
    try:
        st = os.stat(p)
    except OSError:
        return {"path": p, "exists": False}
    out: Dict[str, Any] = {"path": p, "exists": True, "size": st.st_size, "mtime": int(st.st_mtime)}
    if st.st_size <= max_hash_bytes:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out["sha256"] = h.hexdigest()
    else:
        out["sha256"] = None
        out["note"] = f"larger than {max_hash_bytes} bytes: identified by size and mtime only"
    return out


# ------------------------------------------------------------------------------------------------ canary probe
_CANARY = r'''
r() { "$@" >/dev/null 2>&1 && echo yes || echo no; }
echo "src_write=$(r sh -c 'echo x > /workspace/canary_write')"
echo "src_read=$(r test -r /workspace/canary_src.txt)"
echo "data_write=$(r sh -c 'echo x > /data/ds/canary_write')"
echo "data_read=$(r test -r /data/ds/canary_data.txt)"
echo "out_write=$(r sh -c 'echo x > /outputs/canary_out')"
echo "outside_visible=$(r test -e "$CANARY_OUTSIDE")"
echo "home_visible=$(r test -e "$CANARY_SECRET")"
echo "ledger_visible=$(r test -e /workspace/.research/config.yaml)"
echo "env_leak=$([ -n "${CANARY_LEAK:-}" ] && echo yes || echo no)"
echo "ifaces=$(ls /sys/class/net 2>/dev/null | grep -vc '^lo$')"
echo "pids=$(ls /proc 2>/dev/null | grep -c '^[0-9][0-9]*$')"
'''


def probe(pol: Dict[str, Any], runtime: Runtime, timeout: float = 60.0) -> Dict[str, Any]:
    """Run a canary under the same runtime and layout using disposable fixtures. Returns {property: bool|None, "_raw": …}.

    A property that cannot be observed this way is None (reported as unverified), never True."""
    base = tempfile.mkdtemp(prefix="research-canary-")
    try:
        src, ds, out, run, outside, fakehome, ledger = (os.path.join(base, n) for n in
                                                       ("src", "ds", "out", "run", "outside", "home", "ledger"))
        for d in (src, ds, out, run, outside, fakehome, os.path.join(src, ".research")):
            os.makedirs(d)
        open(os.path.join(src, "canary_src.txt"), "w").write("src")
        open(os.path.join(src, ".research", "config.yaml"), "w").write("ledger")
        open(os.path.join(ds, "canary_data.txt"), "w").write("data")
        outside_f = os.path.join(outside, "protected.txt")
        secret_f = os.path.join(fakehome, "credential")
        open(outside_f, "w").write("p")
        open(secret_f, "w").write("s")
        p2 = _merge(pol, {"filesystem": {"datasets": [{"path": ds, "name": "ds"}], "read_only": [], "read_write": []},
                          "image": pol.get("image")})
        mounts = plan(p2, {"source_root": src, "run_dir": run, "outputs_dir": out, "ledger_root": ledger})
        env = job_env(p2, {"CANARY_OUTSIDE": outside_f, "CANARY_SECRET": secret_f})
        argv, lenv, cleanup = runtime.argv(p2, mounts, _CANARY, "/workspace", env)
        os.environ["CANARY_LEAK"] = "1"      # present in the launcher, must not reach the job
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=lenv, stdin=subprocess.DEVNULL)
        finally:
            os.environ.pop("CANARY_LEAK", None)
            for c in cleanup:
                shutil.rmtree(c, ignore_errors=True)
        seen = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        if r.returncode != 0 or "out_write" not in seen:
            return {"_ok": False, "_raw": (r.stderr or r.stdout)[-600:], "_returncode": r.returncode}
        no = lambda k: seen.get(k) == "no"
        fs = p2["filesystem"]
        res: Dict[str, Any] = {
            "filesystem.outputs_scoped": seen.get("out_write") == "yes" and no("outside_visible"),
            "filesystem.outside_hidden": no("outside_visible"),
            "filesystem.home_hidden": no("home_visible"),
            "filesystem.ledger_hidden": no("ledger_visible"),
            "filesystem.source_readonly": (no("src_write") and seen.get("src_read") == "yes") if fs["source"] == "read_only" else None,
            "filesystem.datasets_readonly": no("data_write") and seen.get("data_read") == "yes",
            "env.clean": no("env_leak"),
            "network.disabled": (seen.get("ifaces") == "0") if p2["network"]["mode"] == "disabled" else None,
            "pid.isolated": (seen.get("pids", "999").isdigit() and int(seen["pids"]) < 30),
            "gpu.access": None,   # cannot be observed with a disposable fixture; reported unverified
            "_ok": True, "_raw": r.stdout,
        }
        return res
    finally:
        shutil.rmtree(base, ignore_errors=True)


def choose_runtime(pol: Dict[str, Any]) -> Tuple[Optional[Runtime], List[str]]:
    """Pick the runtime for a policy. 'auto' takes the first available one in AUTO_ORDER that has an image if it needs one."""
    notes: List[str] = []
    if pol["runtime"] != "auto":
        return RUNTIMES.get(pol["runtime"]), notes
    for name in AUTO_ORDER:
        rt = RUNTIMES.get(name)
        if not rt:
            continue
        d = rt.detect()
        if not d["available"]:
            notes.append(f"{name}: unavailable ({'; '.join(d['notes'])})")
        elif name == "apptainer" and not pol.get("image"):
            notes.append("apptainer: skipped because the profile has no image")
        else:
            return rt, notes
    return None, notes


def preflight(pol: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None, run_probe: bool = False) -> Dict[str, Any]:
    """Everything needed to decide whether a job may start. Never raises for 'cannot enforce': it reports and sets `allowed`."""
    pol = normalize(pol)
    wanted = requested_properties(pol)
    rt, notes = choose_runtime(pol)
    out: Dict[str, Any] = {"runtime": rt.name if rt else None, "requested_runtime": pol["runtime"], "notes": notes,
                           "properties": [], "allowed": True, "refusal": None, "mounts": [], "strict": bool(wanted)}
    if pol["runtime"] == "native":
        out["notes"].append("native: no isolation is requested or applied.")
        return out
    if not rt:
        out.update(allowed=False, refusal="No usable isolation runtime: " + ("; ".join(notes) or "none configured") +
                   ". The job was NOT run unrestricted.")
        out["properties"] = [{"property": w, "requested": True, "enforceable": False, "verified": False, "state": "unavailable"} for w in wanted]
        return out
    det = rt.detect()
    out["runtime_info"] = {k: det.get(k) for k in ("version", "notes", "path")}
    if not det["available"]:
        out.update(allowed=False, refusal=f"Runtime {rt.name} is not available: {'; '.join(det['notes'])}. The job was NOT run unrestricted.")
    if ctx:
        try:
            out["mounts"] = [m.as_dict() for m in plan(pol, ctx)]
        except IsolationError as e:
            out.update(allowed=False, refusal=str(e), refusal_hint=e.hint)
    results: Dict[str, Any] = {}
    if det["available"] and run_probe and out["allowed"]:
        try:
            results = probe(pol, rt)
        except (IsolationError, OSError, subprocess.TimeoutExpired) as e:
            results = {"_ok": False, "_raw": str(e)}
        out["probe_ok"] = bool(results.get("_ok"))
        if not results.get("_ok"):
            out["probe_output"] = results.get("_raw")
    for w in wanted:
        can = det["available"] and w in rt.capabilities
        v = results.get(w) if results.get("_ok") else None
        state = "verified" if v is True else "unavailable" if (not can or v is False) else "enforceable"
        out["properties"].append({"property": w, "requested": True, "enforceable": bool(can) and v is not False,
                                  "verified": v, "state": state})
    bad = [x["property"] for x in out["properties"] if x["state"] == "unavailable"]
    if bad and out["allowed"]:
        out.update(allowed=False, refusal="Requested restrictions cannot be enforced: " + ", ".join(bad) + ". The job was NOT run unrestricted.")
    if run_probe and out["allowed"] and pol["enforcement"].get("verify_mounts", True) and not results.get("_ok"):
        out.update(allowed=False, refusal="The isolation self-test failed, so the restrictions are unverified: "
                                           + str(results.get("_raw", ""))[-300:] + ". The job was NOT run unrestricted.")
    return out
