"""`research self-update`: install the newest GitHub release wheel, sha256-verified. Network and install are injectable.

Also holds the version-compatibility rule the extension applies to the core (`core_satisfies`, mirrored in the extension's util.ts).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from . import __version__

REPO = "yubrajbhandari923/research-vscode"


def _nums(v: str):
    return [int(x) for x in str(v).split("+")[0].split("-")[0].split(".")[:3] if x.isdigit()]


def core_satisfies(core_version: str, required: str) -> bool:
    """`required` like "3.0": same major as the core, and the core's minor >= required minor."""
    c, r = _nums(core_version) + [0, 0], _nums(required) + [0, 0]
    return c[0] == r[0] and c[1] >= r[1]


EXTRAS = {"discord": "discord"}   # extra name -> module that proves it is installed


def installed_extras() -> list:
    import importlib.util
    return [e for e, mod in EXTRAS.items() if importlib.util.find_spec(mod) is not None]


class UpdateError(Exception):
    pass


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "research-os-selfupdate", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _install(wheel: str, extras=None) -> int:
    extras = installed_extras() if extras is None else extras
    spec = f"research-panel[{','.join(extras)}] @ {pathlib.Path(wheel).resolve().as_uri()}" if extras else wheel
    pre = sys.prefix.replace("\\", "/")
    if "/uv/tools/" in pre and shutil.which("uv"):
        cmd = ["uv", "tool", "install", "--force", spec]
    elif "/pipx/venvs/" in pre and shutil.which("pipx"):
        cmd = ["pipx", "install", "--force", spec]
    elif subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    elif shutil.which("uv"):
        cmd = ["uv", "pip", "install", "--python", sys.executable, spec]
    else:
        raise UpdateError("no installer found (pip, uv or pipx)")
    return subprocess.run(cmd).returncode


def _sha_ok(path: str, sums: str) -> bool:
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    name = os.path.basename(path)
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower() == h
    return False


def _restart_daemons(restart, out) -> None:
    """Restart the supervisor of every known project that runs older code. The process running this code is still the OLD
    version, so the new install does it in a fresh interpreter (`restart` is injectable for tests)."""
    try:
        from . import machine
        roots = machine.known_projects()
        if restart is None:
            def restart(root):
                code = "import sys;from research import daemonlib;print(daemonlib.ensure_current(sys.argv[1]).get('action'))"
                r = subprocess.run([sys.executable, "-c", code, root], capture_output=True, text=True, timeout=180)
                return r.stdout.strip() or "none"
        for root in roots:
            if restart(root) == "restart":
                out(f"restarted the supervisor for {root}")
    except Exception as e:     # the install already succeeded; a daemon left on old code is reported, not fatal
        out(f"note: could not restart supervisors ({e}); run `research supervisor restart`")


def run(check=False, version=None, repo=REPO, major=False, from_file=None, fetch=_fetch, install=_install,
        current=__version__, out=print, restart=None) -> int:
    try:
        tmp = tempfile.mkdtemp(prefix="research-update-")
        try:
            if from_file:
                wheel = os.path.abspath(from_file)
                if not os.path.isfile(wheel):
                    raise UpdateError(f"no such file: {wheel}")
                sums_path = os.path.join(os.path.dirname(wheel), "SHA256SUMS")
                if os.path.isfile(sums_path) and not _sha_ok(wheel, open(sums_path).read()):
                    raise UpdateError("checksum mismatch against sibling SHA256SUMS; install untouched")
            else:
                api = f"https://api.github.com/repos/{repo}/releases/" + (f"tags/v{version.lstrip('v')}" if version else "latest")
                rel = json.loads(fetch(api))
                latest = str(rel.get("tag_name", "")).lstrip("v")
                assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
                wname = next((n for n in assets if n.endswith(".whl")), None)
                if not latest or not wname or "SHA256SUMS" not in assets:
                    raise UpdateError("release has no wheel and SHA256SUMS")
                cl, ll = _nums(current), _nums(latest)
                breaking = ll[:1] > cl[:1]
                out(f"current {current}, latest {latest}" + (" (major upgrade: breaking changes, needs --major)" if breaking else ""))
                if check:
                    out("up to date" if ll <= cl else "update available: run `research self-update`")
                    return 0
                if not version and ll <= cl:
                    out("already up to date")
                    return 0
                if breaking and not major:
                    raise UpdateError("major upgrade refused; re-run with --major")
                wheel = os.path.join(tmp, wname)
                with open(wheel, "wb") as f:
                    f.write(fetch(assets[wname]))
                if not _sha_ok(wheel, fetch(assets["SHA256SUMS"]).decode()):
                    raise UpdateError("checksum mismatch; install untouched")
            if check:
                return 0
            if install(wheel) != 0:
                raise UpdateError("installer failed; see its output above")
            out("updated; restart the VS Code window to use the new core")
            _restart_daemons(restart, out)
            return 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except UpdateError as e:
        out(f"self-update failed: {e}")
    except Exception as e:  # network, JSON, I/O: the existing install is never touched before install()
        out(f"self-update failed: {e}")
    return 1
