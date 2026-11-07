"""`research self-update`: install the newest GitHub release wheel, sha256-verified. Network and install are injectable."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from . import __version__
from .compat import _nums

REPO = "yubrajbhandari923/research-vscode"


class UpdateError(Exception):
    pass


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "research-os-selfupdate", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _install(wheel: str) -> int:
    pre = sys.prefix.replace("\\", "/")
    if "/uv/tools/" in pre and shutil.which("uv"):
        cmd = ["uv", "tool", "install", "--force", wheel]
    elif "/pipx/venvs/" in pre and shutil.which("pipx"):
        cmd = ["pipx", "install", "--force", wheel]
    elif subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode == 0:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", wheel]
    elif shutil.which("uv"):
        cmd = ["uv", "pip", "install", "--python", sys.executable, wheel]
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


def run(check=False, version=None, repo=REPO, major=False, from_file=None, fetch=_fetch, install=_install,
        current=__version__, out=print) -> int:
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
            return 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except UpdateError as e:
        out(f"self-update failed: {e}")
    except Exception as e:  # network, JSON, I/O: the existing install is never touched before install()
        out(f"self-update failed: {e}")
    return 1
