"""The project's Python environment (local) and how Research OS uses it.

One setting, `environment.python` (an interpreter or a venv folder), is used everywhere code runs on this machine: agent sessions (a
`python`/`python3` shim first on PATH), supervisor/task checks (`{python}` in a `cmd:` check expands to it and `python3` resolves to it),
and `research env check`. Creating the environment is optional and uses `uv` when present; Research OS never installs packages
into an environment it did not create unless you ask (`research env install`).
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

from .store import Project
from .util import ResearchError


def resolve_python(p: Project) -> Optional[str]:
    """The configured interpreter (a file, or <dir>/bin/python of a venv). None when unset or missing."""
    v = (p.config.get("environment") or {}).get("python")
    if not v:
        return None
    v = os.path.expanduser(str(v))
    if not os.path.isabs(v):
        v = os.path.join(os.path.dirname(p.root) if os.path.isdir(os.path.join(os.path.dirname(p.root), v)) else p.root, v)
    cand = [v] if os.path.isfile(v) else [os.path.join(v, "bin", "python"), os.path.join(v, "Scripts", "python.exe")]
    return next((c for c in cand if os.path.isfile(c)), None)


def shim_dir(p: Project) -> Optional[str]:
    """A per-machine folder (inside .research/bin, which is git-ignored) with python/python3 pointing at the configured interpreter."""
    py = resolve_python(p)
    if not py:
        return None
    d = os.path.join(p.rdir, "bin", "py")
    os.makedirs(d, exist_ok=True)
    for name in ("python", "python3"):       # a launcher, not a symlink: a symlinked venv python would not find its pyvenv.cfg
        link = os.path.join(d, name)
        body = f'#!/bin/sh\nexec {shlex.quote(py)} "$@"\n'
        if os.path.isfile(link) and not os.path.islink(link) and open(link).read() == body:
            continue
        if os.path.lexists(link):
            os.unlink(link)
        with open(link, "w") as f:
            f.write(body)
        os.chmod(link, 0o755)
    return d


def path_with_python(p: Project, base: Optional[str] = None) -> str:
    d = shim_dir(p)
    cur = base if base is not None else os.environ.get("PATH", "")
    return (d + os.pathsep + cur) if d else cur


def expand(p: Project, command: str) -> str:
    """`{python}` in a check command becomes the quoted interpreter (the system python3 if none is configured)."""
    if "{python}" not in command:
        return command
    return command.replace("{python}", shlex.quote(resolve_python(p) or shutil.which("python3") or sys.executable))


# Distributions whose import name is not their name. Keys are normalized (lowercase, `-`/`_`/`.` -> `-`). Unlisted names import as themselves.
IMPORT_NAMES = {"pyyaml": "yaml", "scikit-learn": "sklearn", "scikit-image": "skimage", "opencv-python": "cv2", "opencv-python-headless": "cv2",
                "opencv-contrib-python": "cv2", "pillow": "PIL", "beautifulsoup4": "bs4", "python-dateutil": "dateutil", "protobuf": "google.protobuf",
                "attrs": "attr", "pyserial": "serial", "python-dotenv": "dotenv", "pymupdf": "fitz", "pytorch-lightning": "pytorch_lightning",
                "msgpack-python": "msgpack", "python-igraph": "igraph", "pyzmq": "zmq", "pycryptodome": "Crypto", "biopython": "Bio",
                "umap-learn": "umap", "ruamel-yaml": "ruamel.yaml", "tensorflow-cpu": "tensorflow", "faiss-cpu": "faiss", "faiss-gpu": "faiss"}


def import_name(spec: str) -> str:
    """`PyYAML>=6` -> `yaml`: a requirement string (extras, version specifiers, markers) to the module `import` needs."""
    name = re.split(r"[\[\s;<>=!~@(]", spec.strip(), maxsplit=1)[0]
    return IMPORT_NAMES.get(re.sub(r"[-_.]+", "-", name).lower()) or name.replace("-", "_")


def check(p: Project, packages: Optional[List[str]] = None) -> Dict[str, Any]:
    py = resolve_python(p)
    cfg = p.config.get("environment") or {}
    pkgs = list(packages if packages is not None else (cfg.get("packages") or []))
    out: Dict[str, Any] = {"configured": cfg.get("python"), "python": py, "version": None, "packages": {}, "ok": False, "problems": [], "uv": shutil.which("uv")}
    if not py:
        out["problems"].append("environment.python is not set, or the interpreter does not exist" if cfg.get("python") else
                               "No project Python is configured: agents and checks use whatever `python3` is on PATH")
        return out
    r = subprocess.run([py, "-c", "import sys;print(sys.version.split()[0])"], capture_output=True, text=True, timeout=30)
    out["version"] = r.stdout.strip() or None
    for pk in pkgs:
        mod = import_name(pk)
        ok = subprocess.run([py, "-c", f"import {mod}"], capture_output=True, text=True, timeout=60).returncode == 0
        out["packages"][pk] = ok
        if not ok:
            out["problems"].append(f"package {pk!r} cannot be imported by {py}")
    out["ok"] = bool(out["version"]) and not out["problems"]
    return out


def create(path: str, packages: Optional[List[str]] = None, python: Optional[str] = None) -> Dict[str, Any]:
    """Create a venv with uv and install packages. Never touches an existing folder."""
    uv = shutil.which("uv")
    if not uv:
        raise ResearchError("uv is not installed", hint="Install it (https://docs.astral.sh/uv/) or create the environment yourself and set environment.python.")
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(path) and os.listdir(path):
        raise ResearchError(f"{path} already exists and is not empty", hint="Point environment.python at it instead of creating it.")
    cmd = [uv, "venv", path] + (["--python", python] if python else [])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise ResearchError("uv venv failed: " + (r.stderr or r.stdout).strip()[-300:])
    py = os.path.join(path, "bin", "python")
    if packages:
        r = subprocess.run([uv, "pip", "install", "--python", py, *packages], capture_output=True, text=True, timeout=900)
        if r.returncode:
            raise ResearchError("uv pip install failed: " + (r.stderr or r.stdout).strip()[-300:])
    return {"path": path, "python": py, "packages": list(packages or [])}


def install(p: Project, packages: List[str]) -> Dict[str, Any]:
    py = resolve_python(p)
    uv = shutil.which("uv")
    if not py or not uv:
        raise ResearchError("needs environment.python and uv")
    r = subprocess.run([uv, "pip", "install", "--python", py, *packages], capture_output=True, text=True, timeout=900)
    if r.returncode:
        raise ResearchError("uv pip install failed: " + (r.stderr or r.stdout).strip()[-300:])
    return {"installed": packages}
