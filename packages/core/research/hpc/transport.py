"""SSH/rsync transport to a cluster login node.

Thin, auditable wrapper over the system `ssh`, `rsync` and `scp`. Every call is non-interactive
(`BatchMode=yes`), has an explicit timeout, and passes arguments as a list — remote command strings are
assembled with `shlex.quote`, never by string interpolation of user data.

Authentication is entirely OpenSSH's: aliases, jump hosts, keys and agents come from `~/.ssh/config`.
Research OS stores and reads no secrets.

Tests substitute a fake transport (`research.hpc.transport.set_runner`), so CI needs no cluster.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..util import ResearchError, sha256_bytes

DEFAULT_TIMEOUT = 60.0
CONNECT_TIMEOUT = 15
# Retried once, briefly: these are transport hiccups, never "the job failed".
TRANSIENT = ("connection timed out", "connection refused", "connection reset", "broken pipe",
             "temporary failure in name resolution", "no route to host", "connection closed by remote host")

# Swappable for tests. Signature: (argv, timeout) -> CompletedProcess-like.
_runner: Optional[Callable[[List[str], float], Any]] = None


def set_runner(fn: Optional[Callable[[List[str], float], Any]]) -> None:
    """Install a fake process runner (tests). Pass None to restore the real one."""
    global _runner
    _runner = fn


class Result:
    def __init__(self, argv: List[str], returncode: int, stdout: str, stderr: str, seconds: float = 0.0):
        self.argv, self.returncode, self.stdout, self.stderr, self.seconds = argv, returncode, stdout, stderr, seconds

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout or "") + (self.stderr or "")

    def __repr__(self) -> str:
        return f"<Result rc={self.returncode} {' '.join(self.argv[:3])}…>"


def _execute(argv: List[str], timeout: float) -> Result:
    if _runner is not None:
        r = _runner(argv, timeout)
        return r if isinstance(r, Result) else Result(argv, r.returncode, r.stdout or "", r.stderr or "")
    if not shutil.which(argv[0]):
        raise ResearchError(f"`{argv[0]}` is not on PATH on this machine",
                            hint="Install OpenSSH (ssh, scp) and rsync, or set `transfer.method` to a "
                                 "tool you do have.")
    t0 = time.time()
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(argv, 124, "", f"timed out after {timeout:g}s", time.time() - t0)
    return Result(argv, cp.returncode, cp.stdout or "", cp.stderr or "", time.time() - t0)


def _transient(r: Result) -> bool:
    low = r.output.lower()
    return r.returncode == 255 and any(t in low for t in TRANSIENT)


class Ssh:
    """One cluster's connection. Read operations retry once; nothing that could run a job ever retries."""

    def __init__(self, host: str, *, timeout: float = DEFAULT_TIMEOUT, login_shell: bool = True,
                 shell: str = "bash"):
        if not host:
            raise ResearchError("this cluster has no `ssh_host` configured",
                                hint="Set `hpc.clusters.<name>.ssh_host` to an alias you can already "
                                     "`ssh` to; authentication stays in ~/.ssh/config.")
        self.host = host
        self.timeout = timeout
        self.login_shell = login_shell
        self.shell = shell

    # ------------------------------------------------------------------ primitives
    def _ssh_argv(self, remote_cmd: str) -> List[str]:
        return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
                "-o", "StrictHostKeyChecking=accept-new", self.host, self._wrap(remote_cmd)]

    def _wrap(self, remote_cmd: str) -> str:
        """Run the command through a login shell.

        `ssh host cmd` gets a non-interactive shell, which on many clusters skips the very profile
        scripts that put Slurm on PATH: `ssh host sbatch` fails while an interactive `ssh host` then
        `sbatch` works. A login shell reproduces what the user sees by hand.

        Set `login_shell: false` on the cluster if your site's profile is slow or does something
        unwanted non-interactively; the command then runs exactly as `ssh host cmd` would.
        """
        if not self.login_shell:
            return remote_cmd
        return f"{self.shell} -lc {shlex.quote(remote_cmd)}"

    def run(self, argv: Sequence[str], *, timeout: Optional[float] = None, retry: bool = False,
            check: bool = False) -> Result:
        """Run one command remotely. `argv` is a list and is quoted; no shell injection from our side."""
        cmd = " ".join(shlex.quote(str(a)) for a in argv)
        return self.run_shell(cmd, timeout=timeout, retry=retry, check=check)

    def run_shell(self, remote_cmd: str, *, timeout: Optional[float] = None, retry: bool = False,
                  check: bool = False) -> Result:
        """Run a remote shell snippet that we built ourselves (its arguments are already quoted)."""
        r = _execute(self._ssh_argv(remote_cmd), timeout or self.timeout)
        if retry and _transient(r):
            time.sleep(2.0)
            r = _execute(self._ssh_argv(remote_cmd), timeout or self.timeout)
        if check and not r.ok:
            raise ResearchError(f"remote command failed on {self.host}: {r.output.strip()[-400:] or r.returncode}",
                                hint=f"Try it yourself: ssh {self.host} {remote_cmd!r}")
        return r

    # ------------------------------------------------------------------ probes
    def alive(self) -> Result:
        return self.run(["true"], timeout=30.0, retry=True)

    def which(self, *programs: str) -> Dict[str, Optional[str]]:
        """Locate several programs in one round trip."""
        script = "; ".join(f"printf '%s\\t%s\\n' {shlex.quote(prog)} \"$(command -v {shlex.quote(prog)} "
                           f"2>/dev/null)\"" for prog in programs)
        r = self.run_shell(script, retry=True)
        out: Dict[str, Optional[str]] = {prog: None for prog in programs}
        for line in r.stdout.splitlines():
            if "\t" in line:
                name, _, loc = line.partition("\t")
                out[name.strip()] = loc.strip() or None
        return out

    def test_dir(self, path: str) -> Dict[str, Any]:
        """Existence, readability and writability of one remote directory."""
        q = shlex.quote(path)
        r = self.run_shell(
            f"if [ -d {q} ]; then printf 'dir\\n'; "
            f"[ -r {q} ] && printf 'read\\n'; [ -w {q} ] && printf 'write\\n'; "
            f"elif [ -e {q} ]; then printf 'notdir\\n'; else printf 'absent\\n'; fi", retry=True)
        flags = set(r.stdout.split())
        return {"path": path, "exists": "dir" in flags or "notdir" in flags, "is_dir": "dir" in flags,
                "readable": "read" in flags, "writable": "write" in flags, "ok": r.ok}

    def mkdir(self, *paths: str) -> Result:
        """Create remote directories. Only ever creates; never removes anything."""
        return self.run_shell("mkdir -p " + " ".join(shlex.quote(x) for x in paths), check=True)

    def read_file(self, path: str, max_bytes: int = 1 << 20) -> Optional[str]:
        r = self.run_shell(f"head -c {int(max_bytes)} {shlex.quote(path)} 2>/dev/null")
        return r.stdout if r.ok else None

    def write_file(self, path: str, text: str) -> Result:
        """Write a small file remotely (manifests, tokens).

        The content travels in a quoted heredoc rather than being interpolated into the command, and the
        delimiter is quoted so the remote shell performs no expansion on it. One code path, so the fake
        transport in the tests exercises exactly what a real cluster would run.
        """
        delim = "RESEARCH_EOF_" + sha256_bytes(text.encode("utf-8"))[:16]
        body = text if text.endswith("\n") else text + "\n"
        r = self.run_shell(f"cat > {shlex.quote(path)} <<'{delim}'\n{body}{delim}\n")
        if not r.ok:
            raise ResearchError(f"could not write {path} on {self.host}: {r.output.strip()[-300:]}")
        return r

    def listdir(self, path: str, limit: int = 500) -> List[str]:
        """Immediate entries of a remote directory. Never recursive: dataset trees can be enormous."""
        r = self.run_shell(f"ls -1A {shlex.quote(path)} 2>/dev/null | head -n {int(limit)}")
        return [x for x in r.stdout.splitlines() if x.strip()]

    # ------------------------------------------------------------------ transfer
    def push(self, local_dir: str, remote_dir: str, *, method: str = "rsync",
             timeout: float = 1800.0, delete: bool = False) -> Result:
        """Upload a directory. `delete` is never passed by Research OS: remote data is never removed."""
        if delete:
            raise ResearchError("refusing a destructive sync: Research OS never deletes remote files")
        local = local_dir.rstrip("/") + "/"
        if method == "rsync" and shutil.which("rsync"):
            argv = ["rsync", "-az", "--no-perms", "--chmod=ugo=rwX", "--timeout=300",
                    "-e", f"ssh -o BatchMode=yes -o ConnectTimeout={CONNECT_TIMEOUT} "
                          "-o StrictHostKeyChecking=accept-new",
                    local, f"{self.host}:{remote_dir}/"]
        else:
            argv = ["scp", "-r", "-B", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
                    "-o", "StrictHostKeyChecking=accept-new", local_dir, f"{self.host}:{remote_dir}"]
        r = _execute(argv, timeout)
        if not r.ok:
            raise ResearchError(f"upload to {self.host}:{remote_dir} failed: {r.output.strip()[-400:]}",
                                hint="Check the remote path is writable (`research hpc doctor`).")
        return r

    def pull(self, remote_path: str, local_dir: str, *, method: str = "rsync",
             timeout: float = 1800.0) -> Result:
        os.makedirs(local_dir, exist_ok=True)
        if method == "rsync" and shutil.which("rsync"):
            argv = ["rsync", "-az", "--timeout=300",
                    "-e", f"ssh -o BatchMode=yes -o ConnectTimeout={CONNECT_TIMEOUT} "
                          "-o StrictHostKeyChecking=accept-new",
                    f"{self.host}:{remote_path}", local_dir + "/"]
        else:
            argv = ["scp", "-r", "-B", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
                    "-o", "StrictHostKeyChecking=accept-new", f"{self.host}:{remote_path}", local_dir]
        r = _execute(argv, timeout)
        if not r.ok:
            raise ResearchError(f"download from {self.host}:{remote_path} failed: {r.output.strip()[-400:]}")
        return r


def connect(cluster: Dict[str, Any]) -> Ssh:
    if (cluster.get("transport") or "ssh") != "ssh":
        raise ResearchError(f"cluster {cluster.get('name')!r} uses transport "
                            f"{cluster.get('transport')!r}, not ssh")
    return Ssh(cluster.get("ssh_host") or "",
               login_shell=cluster.get("login_shell", True),
               shell=cluster.get("remote_shell") or "bash")
