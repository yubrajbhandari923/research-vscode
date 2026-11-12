"""Keep running after you log out: no tmux, no nohup, no open SSH session.

The supervisor, the chat gateway and every agent are started in their own session with no terminal, so closing a terminal or an SSH connection does not
signal them. Two things on a real machine can still stop them, and `check` looks for both instead of assuming:

* systemd-logind. With `KillUserProcesses=yes` (or when the user's systemd instance stops at the last logout) everything the user started is killed when
  the last session closes, whatever it was detached from. The cure is the machine's own: run the supervisor as a `systemd --user` service and enable
  lingering for the user (`install` does both), which also brings it back after a reboot or a crash.
* a forwarded SSH agent. If the cluster is reached with keys forwarded from your laptop, that agent disappears with your SSH session and every later
  `ssh`/`rsync` to the cluster fails. Nothing here can fix that; `check` says so and names the remedies (a key on this machine, or a ControlMaster).

`install` writes one unit per project (`research-os-<name>.service`) and one for the machine's chat gateway. Units use `KillMode=process`, so restarting
the supervisor never kills the agents it launched. Nothing needs root except lingering on machines whose policy requires it; then the exact command is shown.
"""
from __future__ import annotations

import getpass
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

from .store import Project
from .util import ResearchError


def _run(*argv: str, timeout: float = 15.0) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def has_systemd_user() -> bool:
    if not shutil.which("systemctl"):
        return False
    r = _run("systemctl", "--user", "show-environment")
    return bool(r and r.returncode == 0)


def lingering() -> Optional[bool]:
    """True/False from logind, None when it cannot be known (no logind)."""
    if not shutil.which("loginctl"):
        return None
    r = _run("loginctl", "show-user", getpass.getuser(), "-p", "Linger")
    if not r or r.returncode != 0:
        return None
    return "Linger=yes" in r.stdout


def kill_user_processes() -> Optional[bool]:
    """logind's KillUserProcesses from its configuration files (drop-ins override the main file). None = no logind configuration found."""
    files = ["/usr/lib/systemd/logind.conf", "/etc/systemd/logind.conf"]
    for d in ("/usr/lib/systemd/logind.conf.d", "/run/systemd/logind.conf.d", "/etc/systemd/logind.conf.d"):
        files += sorted(glob.glob(os.path.join(d, "*.conf")))
    val, seen = None, False
    for f in files:
        try:
            for line in open(f, encoding="utf-8", errors="replace"):
                m = re.match(r"\s*KillUserProcesses\s*=\s*(\S+)", line)
                if m:
                    val = m.group(1).lower() in ("yes", "true", "1", "on")
            seen = True
        except OSError:
            continue
    return (val or False) if seen else None


def unit_name(root: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "-", os.path.basename(os.path.abspath(root)) or "project")
    return f"research-os-{base}-{hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:6]}.service"


GATEWAY_UNIT = "research-os-chat.service"


def unit_dir() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "systemd", "user")


def _env_lines() -> List[str]:
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    keep = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(x for x in (pkg, os.environ.get("PYTHONPATH", "")) if x)}
    for k in ("HOME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "RESEARCH_HOME", "ANTHROPIC_API_KEY_FILE"):
        if os.environ.get(k):
            keep[k] = os.environ[k]
    return [f'Environment="{k}={v}"' for k, v in keep.items() if v and '"' not in v and "\n" not in v]


def unit_text(root: str) -> str:
    root = os.path.abspath(root)
    return "\n".join([
        "[Unit]", f"Description=Research OS supervisor for {root}", "After=network-online.target", "",
        "[Service]", "Type=simple", f"WorkingDirectory={root}", *_env_lines(),
        f"ExecStart={sys.executable} -m research.supervisord run --root {root}",
        "Restart=on-failure", "RestartSec=10",
        "KillMode=process",        # restarting or stopping the supervisor must never kill the agents it launched
        "", "[Install]", "WantedBy=default.target", ""])


def gateway_text() -> str:
    return "\n".join([
        "[Unit]", "Description=Research OS chat gateway (Discord/Telegram) for this machine", "After=network-online.target", "",
        "[Service]", "Type=simple", *_env_lines(), f"ExecStart={sys.executable} -m research.chat.gateway run",
        "Restart=on-failure", "RestartSec=30", "KillMode=process", "", "[Install]", "WantedBy=default.target", ""])


def _agent_socket_forwarded(env: Dict[str, str]) -> bool:
    sock = env.get("SSH_AUTH_SOCK") or ""
    return bool(sock) and bool(re.search(r"/ssh-[A-Za-z0-9]+/agent\.\d+$", sock)) and bool(env.get("SSH_CONNECTION") or env.get("SSH_CLIENT"))


def _proc_env(pid: Any) -> Dict[str, str]:
    try:
        with open(f"/proc/{int(pid)}/environ", "rb") as f:
            return dict(x.split("=", 1) for x in f.read().decode("utf-8", errors="replace").split("\0") if "=" in x)
    except (OSError, ValueError, TypeError):
        return {}


def check(p: Project) -> Dict[str, Any]:
    """Will this project's work survive closing the terminal / the SSH session? One row per thing that decides it, with the fix when it would not."""
    from . import supervisor as SV
    rows: List[Dict[str, Any]] = []

    def add(what: str, status: str, detail: str, fix: Optional[str] = None) -> None:
        rows.append({"what": what, "status": status, "detail": detail, "fix": fix})
    d = SV.daemon_status(p)
    unit = unit_name(p.root)
    installed = os.path.exists(os.path.join(unit_dir(), unit))
    systemd = has_systemd_user()
    if d.get("running"):
        own = None
        try:
            own = os.getsid(int(d["pid"])) != os.getsid(0)
        except (OSError, ValueError, TypeError):
            pass
        add("supervisor", "ok", f"running (pid {d['pid']})" + (", in its own session with no terminal: closing this one does not signal it" if own else ""))
    else:
        add("supervisor", "warn", "not running", "research supervisor start   (or: research background install)")
    kup, ling = kill_user_processes(), lingering()
    if not shutil.which("loginctl"):
        add("logout", "ok", "this machine has no systemd-logind: nothing kills your processes at logout")
    elif installed and ling:
        add("logout", "ok", f"runs as the user service {unit} with lingering on: it survives logout and starts again after a reboot")
    elif kup:
        add("logout", "fail", "systemd-logind is configured with KillUserProcesses=yes: everything you started is killed when your last session closes, detached or not",
            "research background install" if systemd else f"sudo loginctl enable-linger {getpass.getuser()}   (and ask the admin about KillUserProcesses)")
    elif installed:
        add("logout", "warn", f"the user service {unit} is installed but lingering is off, so it stops when your last session closes", f"loginctl enable-linger {getpass.getuser()}")
    else:
        add("logout", "ok" if kup is False else "warn",
            "detached processes survive logout here (KillUserProcesses is off)" + ("" if kup is False else "; logind's setting could not be read")
            + ". They do not come back after a reboot or a crash.", "research background install   (optional: restarts it after a reboot or a crash)" if systemd else None)
    env = _proc_env(d.get("pid")) if d.get("running") else {}
    if _agent_socket_forwarded(env or dict(os.environ)):
        add("cluster access", "warn", "SSH keys are forwarded from the machine you logged in from (" + (env or os.environ).get("SSH_AUTH_SOCK", "") + "). That agent ends with your SSH "
            "session; afterwards ssh/rsync to the cluster from here fails and HPC submission, polling and collection stop.",
            "put a key for the cluster on this machine (ssh-keygen; ssh-copy-id CLUSTER), or keep a ControlMaster connection (ControlPersist) open; then restart the supervisor from a shell without agent forwarding")
    else:
        add("cluster access", "ok", "no forwarded SSH agent is in use: cluster connections do not depend on your login session")
    try:
        from .chat import gateway as GW
        g = GW.status()
        from . import notify
        if notify.chat_enabled(p):
            add("chat gateway", "ok" if g.get("running") else "warn", "running" if g.get("running") else "not running: no alerts would reach you",
                None if g.get("running") else "research chat gateway start")
    except Exception:    # noqa: BLE001
        pass
    worst = "fail" if any(r["status"] == "fail" for r in rows) else "warn" if any(r["status"] == "warn" for r in rows) else "ok"
    return {"survives_logout": not any(r["status"] == "fail" for r in rows), "status": worst, "rows": rows, "unit": unit, "installed": installed,
            "systemd_user": systemd, "lingering": ling, "kill_user_processes": kup}


def install(p: Project, gateway: bool = True) -> Dict[str, Any]:
    """Run the supervisor (and the chat gateway) as `systemd --user` services with lingering, replacing the hand-started processes."""
    from . import supervisor as SV
    SV.require_human(p, "Installing a background service")
    if not has_systemd_user():
        raise ResearchError("this machine has no `systemd --user` to install a service into",
                            hint="The supervisor already detaches from the terminal (`research supervisor start`); `research background check` says whether logout would stop it.")
    os.makedirs(unit_dir(), exist_ok=True)
    unit = unit_name(p.root)
    with open(os.path.join(unit_dir(), unit), "w", encoding="utf-8") as f:
        f.write(unit_text(p.root))
    units = [unit]
    from . import notify
    if gateway and notify.chat_enabled(p):
        with open(os.path.join(unit_dir(), GATEWAY_UNIT), "w", encoding="utf-8") as f:
            f.write(gateway_text())
        units.append(GATEWAY_UNIT)
    if SV.daemon_status(p).get("running"):       # the service takes over: stop the hand-started one (agents keep running; state is in files)
        SV.daemon_stop(p)
    if GATEWAY_UNIT in units:
        try:
            from .chat import gateway as GW
            if GW.status().get("running"):
                GW.stop()
        except Exception:    # noqa: BLE001
            pass
    _run("systemctl", "--user", "daemon-reload")
    started = {}
    for u in units:
        r = _run("systemctl", "--user", "enable", "--now", u, timeout=60)
        started[u] = bool(r and r.returncode == 0) or (r.stderr.strip()[:300] if r else "systemctl did not answer")
    ling = lingering()
    linger_cmd = None
    if ling is False:
        r = _run("loginctl", "enable-linger", getpass.getuser())
        ling = lingering()
        if not ling:
            linger_cmd = f"sudo loginctl enable-linger {getpass.getuser()}"
    try:
        p.event("project", None, "background_installed", f"user service {unit} installed" + (" (lingering on)" if ling else ""))
    except Exception:    # noqa: BLE001
        pass
    return {"units": units, "started": started, "lingering": ling, "linger_command": linger_cmd, "check": check(p)}


def uninstall(p: Project) -> Dict[str, Any]:
    from . import supervisor as SV
    SV.require_human(p, "Removing a background service")
    unit = unit_name(p.root)
    path = os.path.join(unit_dir(), unit)
    if not os.path.exists(path):
        return {"removed": False, "unit": unit}
    _run("systemctl", "--user", "disable", "--now", unit, timeout=60)
    os.unlink(path)
    _run("systemctl", "--user", "daemon-reload")
    return {"removed": True, "unit": unit, "note": "the chat gateway service, shared by every project on this machine, was left in place"}
