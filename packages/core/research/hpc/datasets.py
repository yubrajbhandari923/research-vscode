"""Shared datasets on the cluster, discovered from the filesystem.

**There is no registry.** The configured `shared_data` directory *is* the source of truth: a dataset is a
directory under it, and what it is is described by a `manifest.json` inside that same directory. Nothing is
indexed centrally, so two projects (and two agent sessions weeks apart) see the same thing by looking.

    <shared_data>/
      ERA5/
        manifest.json          ← identity, version, source, status, expected files
        files/                 ← the data
      AbdomenAtlas/
        ...

Rules that matter:
  * a directory with no manifest is **not** assumed to be valid, and is **not** re-downloaded either —
    an agent or human inspects it and writes a manifest once they have verified what it is
  * `status` is explicit: downloading | complete | incomplete | corrupt. An interrupted download can
    never read as complete, because the marker is written last, atomically
  * versions are distinguished by manifest `version` (and/or a versioned directory); a different version
    never overwrites an existing one
  * verification is layered — a cheap existence/size check by default, checksums only when asked, so a
    terabyte is not rehashed every session
"""
from __future__ import annotations

import json
import posixpath
import shlex
from typing import Any, Dict, List, Optional

from ..util import ResearchError
from . import config as C
from .transport import Ssh

MANIFEST = "manifest.json"
STATUSES = ("downloading", "complete", "incomplete", "corrupt", "unknown")
LOCK = ".research-download.lock"
STAGING = ".research-staging"


def manifest_template(ident: str, **fields: Any) -> Dict[str, Any]:
    """A manifest skeleton. Only fields someone has actually verified should be filled in."""
    m = {
        "id": ident,
        "name": fields.get("name") or ident,
        "version": fields.get("version"),
        "source": fields.get("source"),
        "status": fields.get("status") or "incomplete",
        "description": fields.get("description"),
        "files_directory": fields.get("files_directory") or "files",
        "expected_files": fields.get("expected_files") or [],
        "expected_bytes": fields.get("expected_bytes"),
        "verification": {"method": fields.get("method") or "file-manifest", "verified_at": None,
                         "verified_by": None, "checksums": fields.get("checksums") or {}},
        "preprocessing": fields.get("preprocessing"),
        "created_at": fields.get("created_at"),
        "created_by": fields.get("created_by"),
    }
    return {k: v for k, v in m.items() if v is not None or k in ("version", "source", "description")}


def shared_root(p, cluster: Dict[str, Any]) -> str:
    root = ((cluster.get("paths") or {}).get("shared_data"))
    if not root:
        raise ResearchError(f"cluster {cluster.get('name')!r} has no `paths.shared_data` configured",
                            hint="Set the approved shared dataset directory under "
                                 "`hpc.clusters.<name>.paths.shared_data` in .research/config.yaml.")
    return C.check_remote_path(root, "hpc.clusters.<name>.paths.shared_data")


def _read_manifest(ssh: Ssh, ddir: str) -> Optional[Dict[str, Any]]:
    text = ssh.read_file(posixpath.join(ddir, MANIFEST), max_bytes=1 << 20)
    if not text or not text.strip():
        return None
    try:
        m = json.loads(text)
    except ValueError as e:
        return {"_unparseable": str(e)}
    return m if isinstance(m, dict) else {"_unparseable": "manifest.json is not an object"}


def _probe(ssh: Ssh, root: str, name: str) -> Dict[str, Any]:
    """One dataset directory, described without walking it: entries, manifest, markers."""
    ddir = posixpath.join(root, name)
    q = shlex.quote(ddir)
    r = ssh.run_shell(
        f"cd {q} 2>/dev/null || exit 9; "
        f"[ -f {shlex.quote(MANIFEST)} ] && printf 'manifest\\n'; "
        f"[ -e {shlex.quote(LOCK)} ] && printf 'lock\\n'; "
        f"[ -d {shlex.quote(STAGING)} ] && printf 'staging\\n'; "
        f"printf 'entries\\t%s\\n' \"$(ls -1A | head -n 50 | tr '\\n' ',')\"")
    if r.returncode == 9:
        return {"id": name, "path": ddir, "exists": False}
    flags = set()
    entries: List[str] = []
    for line in r.stdout.splitlines():
        if line.startswith("entries\t"):
            entries = [x for x in line.split("\t", 1)[1].split(",") if x]
        elif line.strip():
            flags.add(line.strip())
    man = _read_manifest(ssh, ddir) if "manifest" in flags else None
    out: Dict[str, Any] = {
        "id": name, "path": ddir, "exists": True, "entries": entries,
        "has_manifest": "manifest" in flags,
        "download_in_progress": "lock" in flags or "staging" in flags,
        "manifest": man,
    }
    if man and man.get("_unparseable"):
        out["status"] = "unknown"
        out["problem"] = f"manifest.json could not be parsed: {man['_unparseable']}"
    elif man:
        out["status"] = man.get("status") or "unknown"
        out["version"] = man.get("version")
        out["name"] = man.get("name") or name
        out["source"] = man.get("source")
        out["files_directory"] = man.get("files_directory") or "files"
    else:
        # Untracked directory: real data may well be here, but nobody has said what it is.
        out["status"] = "unknown"
        out["problem"] = ("no manifest.json: this directory is not described, so it is neither trusted "
                          "nor re-downloaded. Inspect it and write a manifest with "
                          "`research hpc datasets adopt <id>`.")
    if out.get("download_in_progress"):
        out["status"] = "downloading"
    return out


def list_datasets(p, cluster: Dict[str, Any], ssh: Optional[Ssh] = None) -> Dict[str, Any]:
    """Every dataset directory under the configured shared data root."""
    from .transport import connect
    root = shared_root(p, cluster)
    ssh = ssh or connect(cluster)
    names = [n for n in ssh.listdir(root) if not n.startswith(".")]
    items = [_probe(ssh, root, n) for n in sorted(names)]
    return {"cluster": cluster.get("name"), "shared_data": root, "count": len(items), "datasets": items,
            "note": "Discovered by listing the shared data directory. There is no dataset registry; "
                    "each dataset describes itself in its own manifest.json."}


def inspect(p, cluster: Dict[str, Any], ident: str, *, ssh: Optional[Ssh] = None,
            files: bool = False) -> Dict[str, Any]:
    """One dataset. With `files`, also lists the top of its files directory (never a full recursive walk)."""
    from .transport import connect
    root = shared_root(p, cluster)
    ssh = ssh or connect(cluster)
    info = _probe(ssh, root, _safe_id(ident))
    if not info["exists"]:
        raise ResearchError(f"no dataset directory {ident!r} under {root}",
                            hint="`research hpc datasets list` shows what is there.")
    if files:
        fdir = posixpath.join(info["path"], info.get("files_directory") or "files")
        info["files_listing"] = ssh.listdir(fdir, limit=200)
        info["files_dir"] = fdir
    return info


def _safe_id(ident: str) -> str:
    """A dataset id is a single path component. Never a path, never a traversal."""
    ident = str(ident).strip()
    if not ident or "/" in ident or ident.startswith(".") or ident in (".", ".."):
        raise ResearchError(f"invalid dataset id {ident!r}: it must be a single directory name "
                            "under the shared data root")
    return ident


def check(p, cluster: Dict[str, Any], ident: str, *, ssh: Optional[Ssh] = None,
          deep: bool = False) -> Dict[str, Any]:
    """Verify a dataset is usable.

    Three strengths, reported explicitly so nobody mistakes one for another:
      * `manifest`  — the manifest says complete (weakest; trusts whoever wrote it)
      * `inventory` — every expected file exists, and sizes match where recorded
      * `checksum`  — recorded checksums recomputed remotely (`--deep`; expensive on large data)
    """
    from .transport import connect
    ssh = ssh or connect(cluster)
    info = inspect(p, cluster, ident, ssh=ssh)
    man = info.get("manifest") or {}
    out: Dict[str, Any] = {"id": info["id"], "path": info["path"], "status": info.get("status"),
                           "usable": False, "strength": "none", "problems": [], "warnings": [],
                           "checked": 0}
    if info.get("download_in_progress"):
        out["problems"].append("a download is in progress (lock or staging directory present); "
                               "this dataset is not complete yet")
        return out
    if not info["has_manifest"]:
        out["problems"].append(info.get("problem") or "no manifest.json")
        return out
    if man.get("_unparseable"):
        out["status"] = "corrupt"
        out["problems"].append(f"manifest.json is not valid JSON: {man['_unparseable']}")
        return out
    if man.get("status") != "complete":
        out["problems"].append(f"manifest status is {man.get('status')!r}, not 'complete'")
        return out
    out["strength"] = "manifest"
    out["version"] = man.get("version")

    fdir = posixpath.join(info["path"], man.get("files_directory") or "files")
    expected = [str(x) for x in (man.get("expected_files") or [])]
    if expected:
        listing = _stat_files(ssh, fdir, expected)
        missing = [f for f, st in listing.items() if not st["exists"]]
        if missing:
            out["problems"].append(f"{len(missing)} expected file(s) missing, e.g. {', '.join(missing[:5])}")
            return out
        sizes = (man.get("verification") or {}).get("sizes") or {}
        wrong = [f for f, want in sizes.items()
                 if f in listing and listing[f]["size"] is not None and int(want) != listing[f]["size"]]
        if wrong:
            out["problems"].append(f"{len(wrong)} file(s) have an unexpected size, e.g. {', '.join(wrong[:5])}")
            return out
        out["strength"] = "inventory"
        out["checked"] = len(expected)
    else:
        out["warnings"].append("the manifest lists no expected_files, so only its own status word was "
                               "checked — that is a weaker guarantee than an inventory check")

    checks = (man.get("verification") or {}).get("checksums") or {}
    if deep:
        if not checks:
            out["warnings"].append("--deep was requested but the manifest records no checksums")
        else:
            bad = _verify_checksums(ssh, fdir, checks)
            if bad:
                out["status"] = "corrupt"
                out["problems"].append(f"{len(bad)} checksum mismatch(es), e.g. {', '.join(bad[:5])}")
                return out
            out["strength"] = "checksum"
            out["checked"] = len(checks)
    elif checks:
        out["warnings"].append(f"{len(checks)} checksum(s) recorded but not recomputed; pass --deep to verify "
                               "them (this can be expensive on large datasets)")

    out["usable"] = True
    return out


def _stat_files(ssh: Ssh, fdir: str, names: List[str], batch: int = 200) -> Dict[str, Dict[str, Any]]:
    """Existence and size of specific files, in batches, without walking the tree."""
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        script = "; ".join(
            f"f={shlex.quote(posixpath.join(fdir, n))}; "
            f"if [ -f \"$f\" ]; then printf '%s\\t1\\t%s\\n' {shlex.quote(n)} "
            f"\"$(wc -c < \"$f\" | tr -d ' ')\"; else printf '%s\\t0\\t\\n' {shlex.quote(n)}; fi"
            for n in chunk)
        r = ssh.run_shell(script, timeout=120.0)
        seen = set()
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name, exists = parts[0], parts[1] == "1"
                size = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else None
                out[name] = {"exists": exists, "size": size}
                seen.add(name)
        for n in chunk:
            out.setdefault(n, {"exists": False, "size": None})
    return out


def _verify_checksums(ssh: Ssh, fdir: str, checks: Dict[str, Any], batch: int = 50) -> List[str]:
    """Recompute sha256 remotely and return the names that do not match."""
    names = sorted(checks)
    bad: List[str] = []
    tool = ssh.which("sha256sum", "shasum")
    cmd = "sha256sum" if tool.get("sha256sum") else ("shasum -a 256" if tool.get("shasum") else None)
    if not cmd:
        raise ResearchError("neither sha256sum nor shasum is available on the cluster; cannot verify "
                            "checksums there", hint="Drop --deep, or ask your cluster admins.")
    for i in range(0, len(names), batch):
        chunk = names[i:i + batch]
        args = " ".join(shlex.quote(posixpath.join(fdir, n)) for n in chunk)
        r = ssh.run_shell(f"{cmd} {args} 2>/dev/null", timeout=1800.0)
        got = {}
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                got[posixpath.basename(parts[-1])] = parts[0]
        for n in chunk:
            want = checks[n] if isinstance(checks[n], str) else (checks[n] or {}).get("sha256")
            if want and got.get(posixpath.basename(n)) != want:
                bad.append(n)
    return bad


# =============================================================================== staging

def adopt(p, cluster: Dict[str, Any], ident: str, *, ssh: Optional[Ssh] = None,
          version: Optional[str] = None, source: Optional[str] = None, description: Optional[str] = None,
          status: str = "complete", expected_files: Optional[List[str]] = None,
          dry_run: bool = False) -> Dict[str, Any]:
    """Write a manifest for a dataset that already exists on the cluster but is undescribed.

    This records what a human or agent has *verified*; it does not inspect-and-guess. The inventory is
    taken from the files directory so the manifest can be checked later.
    """
    from ..util import now_iso
    from .transport import connect
    ssh = ssh or connect(cluster)
    ident = _safe_id(ident)
    info = inspect(p, cluster, ident, ssh=ssh)
    if info["has_manifest"] and not dry_run:
        raise ResearchError(f"{ident} already has a manifest.json; refusing to overwrite it",
                            hint=f"Read it with `research hpc datasets inspect {ident}`. Edit it on the "
                                 "cluster if it is wrong — Research OS will not rewrite someone else's manifest.")
    if status not in STATUSES:
        raise ResearchError(f"status must be one of {', '.join(STATUSES)}")
    fdir_name = "files" if "files" in (info.get("entries") or []) else "."
    fdir = posixpath.join(info["path"], fdir_name)
    listing = expected_files if expected_files is not None else ssh.listdir(fdir, limit=1000)
    man = manifest_template(
        ident, version=version, source=source, description=description, status=status,
        files_directory=fdir_name, expected_files=sorted(x for x in listing if x != MANIFEST),
        created_at=now_iso(), created_by=p.author.get("author_name") or p.author.get("author_type"))
    text = json.dumps(man, indent=2, sort_keys=True) + "\n"
    if dry_run:
        return {"id": ident, "path": info["path"], "manifest": man, "dry_run": True, "written": False}
    ssh.write_file(posixpath.join(info["path"], MANIFEST), text)
    p.event("dataset", None, "manifest_written",
            f"wrote a manifest for dataset {ident} on {cluster.get('name')} "
            f"({len(man.get('expected_files') or [])} files, status {status})")
    return {"id": ident, "path": info["path"], "manifest": man, "dry_run": False, "written": True}


def fetch(p, cluster: Dict[str, Any], ident: str, *, ssh: Optional[Ssh] = None, url: Optional[str] = None,
          from_local: Optional[str] = None, approve: bool = False, deep: bool = False) -> Dict[str, Any]:
    """Make a dataset available: reuse it if it is already valid, otherwise stage it.

    Reuse comes first and is the normal outcome. Staging needs explicit approval (`approve=True`, i.e.
    `--yes`), because it moves a lot of bytes and may cost money or quota.
    """
    from ..util import now_iso
    from .transport import connect
    ssh = ssh or connect(cluster)
    ident = _safe_id(ident)
    root = shared_root(p, cluster)
    ddir = posixpath.join(root, ident)
    dcfg = cluster.get("datasets") or {}

    existing = _probe(ssh, root, ident)
    if existing["exists"] and dcfg.get("reuse_existing", True):
        if existing.get("download_in_progress"):
            return {"id": ident, "path": ddir, "action": "wait", "reused": False,
                    "detail": "another session is downloading this dataset (lock present); "
                              "nothing was started. Re-run when it finishes."}
        verdict = check(p, cluster, ident, ssh=ssh, deep=deep) if existing["has_manifest"] else None
        if verdict and verdict["usable"]:
            return {"id": ident, "path": ddir, "action": "reuse", "reused": True,
                    "verification": verdict,
                    "detail": f"existing dataset verified ({verdict['strength']} check); reused as-is"}
        if existing["has_manifest"]:
            return {"id": ident, "path": ddir, "action": "blocked", "reused": False,
                    "verification": verdict,
                    "detail": "the dataset exists but did not verify: "
                              + "; ".join((verdict or {}).get("problems") or ["unknown problem"])
                              + ". Nothing was downloaded or deleted — inspect it on the cluster."}
        return {"id": ident, "path": ddir, "action": "blocked", "reused": False,
                "detail": "this directory exists but has no manifest, so it is neither trusted nor "
                          "re-downloaded. Verify what it is, then `research hpc datasets adopt "
                          f"{ident}` to describe it."}

    if not (url or from_local):
        raise ResearchError(f"dataset {ident!r} is not on the cluster and no source was given",
                            hint="Pass --url https://… or --from-local PATH, or stage it yourself and "
                                 f"run `research hpc datasets adopt {ident}`.")
    if dcfg.get("require_download_approval", True) and not approve:
        return {"id": ident, "path": ddir, "action": "needs_approval", "reused": False,
                "source": url or from_local,
                "detail": "staging a new dataset needs approval; re-run with --yes to proceed. "
                          "Nothing was transferred."}
    return _stage(p, ssh, cluster, ident, ddir, url=url, from_local=from_local)


def _stage(p, ssh: Ssh, cluster: Dict[str, Any], ident: str, ddir: str, *, url: Optional[str],
           from_local: Optional[str]) -> Dict[str, Any]:
    """Download or upload into a staging directory, then publish atomically.

    Concurrency is handled by `mkdir` on a lock directory, which is atomic on every POSIX filesystem —
    no distributed locking, no coordination service. The manifest (the completion marker) is written
    last, so an interrupted transfer can never look complete.
    """
    from ..util import now_iso
    q = shlex.quote(ddir)
    lock = shlex.quote(posixpath.join(ddir, LOCK))
    staging = posixpath.join(ddir, STAGING)
    ssh.mkdir(ddir)
    got = ssh.run_shell(f"mkdir {lock} 2>/dev/null && echo acquired || echo busy")
    if "acquired" not in got.stdout:
        return {"id": ident, "path": ddir, "action": "wait", "reused": False,
                "detail": "another session holds the download lock for this dataset; nothing was started."}
    try:
        ssh.mkdir(staging)
        if url:
            if not str(url).lower().startswith(("https://", "http://")):
                raise ResearchError("only http(s) URLs are supported here; stage anything else yourself "
                                    "and use `research hpc datasets adopt`")
            tool = ssh.which("curl", "wget")
            fetcher = (f"curl -fSL --retry 3 -o {shlex.quote(posixpath.join(staging, posixpath.basename(url) or 'download'))} "
                       f"{shlex.quote(url)}" if tool.get("curl") else
                       (f"wget -q -c -O {shlex.quote(posixpath.join(staging, posixpath.basename(url) or 'download'))} "
                        f"{shlex.quote(url)}" if tool.get("wget") else None))
            if not fetcher:
                raise ResearchError("neither curl nor wget is available on the cluster",
                                    hint="Many clusters block outbound network access. Stage the data with "
                                         "your site's transfer tool, then run `research hpc datasets adopt`.")
            r = ssh.run_shell(fetcher, timeout=7200.0)
            if not r.ok:
                raise ResearchError(f"download failed on the cluster: {r.output.strip()[-400:]}",
                                    hint="The partial download is left in "
                                         f"{staging} and the dataset is NOT marked complete.")
        else:
            ssh.push(str(from_local), staging, method=(cluster.get("transfer") or {}).get("method", "rsync"),
                     timeout=7200.0)
        files_dir = posixpath.join(ddir, "files")
        ssh.run_shell(f"mkdir -p {shlex.quote(files_dir)} && "
                      f"cp -R {shlex.quote(staging)}/. {shlex.quote(files_dir)}/ && "
                      f"rm -rf {shlex.quote(staging)}", check=True, timeout=3600.0)
        listing = ssh.listdir(files_dir, limit=1000)
        man = manifest_template(ident, source=url or f"local:{from_local}", status="complete",
                                files_directory="files", expected_files=sorted(listing),
                                created_at=now_iso(),
                                created_by=p.author.get("author_name") or p.author.get("author_type"))
        # written last: the manifest is the completion marker
        ssh.write_file(posixpath.join(ddir, MANIFEST), json.dumps(man, indent=2, sort_keys=True) + "\n")
        p.event("dataset", None, "staged",
                f"staged dataset {ident} on {cluster.get('name')} from {url or from_local} "
                f"({len(listing)} file(s))")
        return {"id": ident, "path": ddir, "action": "staged", "reused": False, "manifest": man,
                "detail": f"staged {len(listing)} file(s) and marked the dataset complete"}
    finally:
        ssh.run_shell(f"rmdir {lock} 2>/dev/null || true")


def resolve(p, cluster: Dict[str, Any], wanted: List[Dict[str, Any]], *, ssh: Optional[Ssh] = None,
            deep: bool = False) -> Dict[str, Any]:
    """Resolve the datasets an experiment declares, before a submission.

    Returns per-dataset verdicts and the paths a job should use. Missing or unverified datasets are
    reported, never silently downloaded.
    """
    from .transport import connect
    if not wanted:
        return {"ok": True, "datasets": [], "paths": {}, "missing": []}
    ssh = ssh or connect(cluster)
    root = shared_root(p, cluster)
    out, paths, missing = [], {}, []
    for w in wanted:
        ident = str(w.get("id") or "").strip()
        if not ident:
            continue
        try:
            verdict = check(p, cluster, ident, ssh=ssh, deep=deep)
        except ResearchError as e:
            verdict = {"id": ident, "usable": False, "problems": [str(e)], "strength": "none",
                       "warnings": []}
        want_version = w.get("version")
        if verdict.get("usable") and want_version and str(verdict.get("version")) != str(want_version):
            verdict = {**verdict, "usable": False,
                       "problems": list(verdict.get("problems") or [])
                       + [f"version {verdict.get('version')!r} is present, but {want_version!r} was requested; "
                          "the existing copy was left untouched"]}
        out.append(verdict)
        if verdict.get("usable"):
            paths[ident] = posixpath.join(root, ident,
                                          (verdict.get("files_directory") or "files"))
        else:
            missing.append(ident)
    return {"ok": not missing, "datasets": out, "paths": paths, "missing": missing}
