"""Selective promotion of experiment code into `main`.

Scientific success, technical correctness, reusability, integration-readiness and promotion are SEPARATE facts: a failed experiment
stays fully documented and simply never reaches integration-ready; a successful one may still contain nothing worth promoting.

Flow (each step is a record in `.research/promotions.jsonl`; nothing is deleted or rewritten, and `main` moves only at the last,
human-confirmed step):

    prepare    a candidate branch `promote/PROMO-n-…` is created from the CURRENT main in its own worktree, and ONLY the selected
               commits (cherry-pick) or paths (taken from the workspace head) are applied. The workspace is never touched.
    diff       the exact patch main would receive.
    verify     the configured test command runs in the candidate; the result is bound to the candidate's head commit.
    evidence   gate: the experiment has a supported finding (configurable); reported with the record, not inferred.
    review     an implementation review by someone other than the preparer.
    approve    a HUMAN approves, bound to the candidate head. Agents are refused.
    integrate  human-confirmed `git merge --ff-only` of the candidate into main. Refused if main moved, main's worktree is dirty, any gate
               is open, or the candidate changed after approval. Never forces, rebases, deletes branches, or pushes.
"""
from __future__ import annotations

import contextlib
import os
import re
from typing import Any, Dict, List, Optional

from . import gitinfo, workspace as W
from .util import PolicyBlocked, ResearchError, now_iso

STATES = ("prepared", "verified", "reviewed", "approved", "promoted", "abandoned")


def _cfg(p) -> Dict[str, Any]:
    return p.config.get("promotion") or {}


def _file(p) -> str:
    return p.path("promotions.jsonl")


def _records(p) -> List[Dict[str, Any]]:
    from .store import read_jsonl_from
    f = _file(p)
    return [r for r, _ in read_jsonl_from(f, 0)] if os.path.exists(f) else []


def _append(p, rec: Dict[str, Any]) -> None:
    from .store import append_jsonl
    append_jsonl(_file(p), {"ts": now_iso(), **rec, **{k: v for k, v in p.author.items() if v}})


_SUP: List[Dict[str, Any]] = []   # in-process only: pushed by autopromote while an authorised plan is being promoted; unreachable from an agent process


@contextlib.contextmanager
def supervisor_authority(authorization: Dict[str, Any]):
    _SUP.append(authorization)
    try:
        yield
    finally:
        _SUP.pop()


def _require_supervisor(p, c: Dict[str, Any]) -> Dict[str, Any]:
    if not _SUP or (p.author or {}).get("author_type") != "system" or (p.author or {}).get("author_name") != "supervisor":
        raise PolicyBlocked("Only the supervisor, under a human plan authorization, may do this")
    if not c.get("auto"):
        raise PolicyBlocked(f"{c['id']} was not prepared by automatic promotion; it needs a human approval")
    return _SUP[-1]


def _is_human(p) -> bool:
    return (p.author or {}).get("author_type") != "agent"


def _fold(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in records:
        c = out.setdefault(r["promotion"], {"id": r["promotion"], "reviews": [], "verifications": [], "state": "prepared"})
        op = r["op"]
        if op == "prepare":
            c.update({k: v for k, v in r.items() if k not in ("op", "promotion")})
            c["prepared_at"] = r["ts"]
            c["prepared_by"] = r.get("author_name")
            c["prepared_by_type"] = r.get("author_type")
        elif op == "verify":
            c["verifications"].append(r)
        elif op == "review":
            c["reviews"].append(r)
        elif op == "approve":
            c["approval"] = r
        elif op == "integrate":
            c["integration"] = r
        elif op == "abandon":
            c["abandoned"] = r
    return out


def _main_branch(p) -> str:
    return _cfg(p).get("main_branch") or "main"


def _top(p) -> str:
    top = W.toplevel(p.root)
    if not top:
        raise ResearchError("Promotion needs a git repository")
    return top


def _git(cwd: str, *args: str) -> str:
    return W._git_ok(cwd, *args)


def _main_worktree(p) -> Optional[Dict[str, Any]]:
    br = _main_branch(p)
    for w in W.worktrees(_top(p)):
        if w["branch"] == br:
            return w
    return None


def _cand_path(p, pid: str) -> Optional[str]:
    common = W.common_dir(_top(p))
    return W._local_paths(common).get(pid) if common else None


def _is_ancestor(cwd: str, ancestor: str, descendant: str) -> bool:
    return W._git(cwd, "merge-base", "--is-ancestor", ancestor, descendant) is not None   # git prints nothing; exit status is the answer


def _head(path: str) -> Optional[str]:
    return W._git(path, "rev-parse", "HEAD")


# ------------------------------------------------------------------------------------------------ states
def _gates(p, c: Dict[str, Any]) -> Dict[str, Any]:
    """Every fact, separately. `state` is derived from them, never stored."""
    head = None
    path = _cand_path(p, c["id"])
    if path and os.path.isdir(path):
        head = _head(path)
    head = head or c.get("candidate_head")
    ver = next((v for v in reversed(c["verifications"]) if v.get("head") == head), None)
    dirty = bool(path and os.path.isdir(path) and W._git(path, "status", "--porcelain"))
    tests = {"state": "unverified", "detail": "No test command has run against the current candidate."}
    if dirty:   # the tree differs from the commit the tests would be bound to, so nothing recorded applies
        tests = {"state": "stale", "detail": "The candidate worktree has uncommitted changes; commit or discard them, then verify again."}
    elif ver:
        tests = {"state": "passed" if ver["ok"] else "failed", "detail": f"exit {ver.get('exit_code')} in {ver.get('seconds')}s",
                 "output": ver.get("output"), "command": ver.get("command")}
    ev = _evidence(p, c)
    sci = _scientific_content(p, c, head)
    rev = [r for r in c["reviews"] if r.get("head") == head]
    verdict = next((r for r in reversed(rev) if r["verdict"] in ("approve", "changes")), None)
    review = {"state": "none", "detail": "No review of the current candidate."}
    if verdict:
        review = {"state": "approved" if verdict["verdict"] == "approve" else "changes_requested", "by": verdict.get("author_name"),
                  "note": verdict.get("note")}
    ap = c.get("approval")
    approval = {"state": "none"}
    if ap:
        approval = {"state": "approved" if ap.get("head") == head else "stale", "by": ap.get("author_name"), "head": ap.get("head"),
                    "detail": None if ap.get("head") == head else "The candidate changed after approval; approve again."}
    return {"head": head, "tests": tests, "evidence": ev, "review": review, "approval": approval, "scientific_content": sci}


def _scientific_content(p, c: Dict[str, Any], head: Optional[str]) -> Dict[str, Any]:
    """Provenance of scientific Quarto content the candidate adds or changes, using the guide's own source checks (identifiers in scientific prose, stale or
    invented attribution, executable cells, captions). A candidate with no .qmd change is `not_applicable` and is never blocked by this."""
    base = c.get("base_main")
    if not head or not base:
        return {"state": "not_applicable", "detail": "No candidate to inspect yet."}
    top = _top(p)
    names = [n for n in (W._git(top, "diff", "--name-only", "--diff-filter=AM", f"{base}..{head}", "--", "*.qmd") or "").splitlines()
             if n and not os.path.basename(n).startswith("_")]
    if not names:
        return {"state": "not_applicable", "detail": "No scientific Quarto content changed: not checked."}
    from . import guide_quarto as GQ
    errs = []
    for n in names:
        text = W._git(top, "show", f"{head}:{n}") or ""
        errs += [i["message"] for i in GQ.text_issues(p, n, text) if i["severity"] == "error"]
    if errs:
        return {"state": "failed", "files": names, "detail": " | ".join(errs[:4]), "issues": errs}
    return {"state": "passed", "files": names, "detail": f"{len(names)} chapter(s) checked: no internal identifiers in scientific prose, every cited record exists"}


def _evidence(p, c: Dict[str, Any]) -> Dict[str, Any]:
    exp = c.get("experiment")
    if c.get("auto"):   # code correctness only: a negative or undecided hypothesis neither blocks nor is endorsed by promotion
        return {"state": "info", "detail": "Automatic code promotion: the scientific status of the experiment is not assessed here and is not implied."}
    if not exp:
        return {"state": "none", "detail": "Not linked to an experiment."}
    fs = [f for f in p.list("finding") if exp in (f.get("links") or {}).get("supports", []) or exp in (f.get("supports") or [])]
    supported = [f["id"] for f in fs if f["status"] == "supported"]
    contradicted = [f["id"] for f in fs if f["status"] == "contradicted"]
    if supported:
        return {"state": "passed", "detail": f"supported finding(s): {', '.join(supported)}", "findings": supported}
    need = _cfg(p).get("require_supported_finding", True)
    return {"state": "blocked" if need else "info", "detail": f"No supported finding for {exp} yet" + (" (required)." if need else "."), "findings": [f["id"] for f in fs], "contradicted": contradicted}


def _classify(p, c: Dict[str, Any], g: Dict[str, Any]) -> Dict[str, Any]:
    """The separate questions. None means not known yet, which is different from no."""
    ev = g["evidence"]["state"]
    return {"scientifically_successful": True if ev == "passed" else False if g["evidence"].get("contradicted") else None,
            "technically_correct": {"passed": True, "failed": False}.get(g["tests"]["state"]),
            "reusable": bool(c.get("selection", {}).get("commits") or c.get("selection", {}).get("paths")),
            "integration_ready": g["tests"]["state"] == "passed" and ev in ("passed", "info") and g["review"]["state"] == "approved"
            and g["scientific_content"]["state"] != "failed"
            and g["approval"]["state"] == "approved",
            "promoted": bool(c.get("integration"))}


def show(p, pid: str) -> Dict[str, Any]:
    c = _fold(_records(p)).get(pid)
    if not c:
        raise ResearchError(f"No promotion {pid}", hint="`research promote list`")
    g = _gates(p, c)
    if c.get("integration"):
        state = "promoted"
    elif c.get("abandoned"):
        state = "abandoned"
    elif g["approval"]["state"] == "approved":
        state = "approved"
    elif g["review"]["state"] == "approved":
        state = "reviewed"
    elif g["tests"]["state"] == "passed":
        state = "verified"
    else:
        state = "prepared"
    path = _cand_path(p, pid)
    return {**{k: v for k, v in c.items() if k not in ("verifications", "reviews")}, "state": state, "gates": g,
            "classification": _classify(p, c, g), "path": path, "present": bool(path and os.path.isdir(path)),
            "verification_count": len(c["verifications"]), "review_count": len(c["reviews"])}


def list_candidates(p, include_closed: bool = False) -> List[Dict[str, Any]]:
    out = [show(p, k) for k in sorted(_fold(_records(p)))]
    return out if include_closed else [c for c in out if c["state"] not in ("promoted", "abandoned")]


# ------------------------------------------------------------------------------------------------ inspect / prepare
def changes(p, ws: str) -> Dict[str, Any]:
    """What an experiment workspace changed relative to its base: commits and files, plus uncommitted work (which cannot be promoted)."""
    s = W.show(p, ws)
    if not s["present"]:
        raise ResearchError(f"{ws} has no directory on this machine")
    path, base = s["path"], s.get("base_rev")
    commits = []
    for line in (W._git(path, "log", "--reverse", "--format=%H%x09%s", f"{base}..HEAD") or "").splitlines():
        sha, _, subject = line.partition("\t")
        files = (W._git(path, "show", "--name-only", "--format=", sha) or "").splitlines()
        commits.append({"sha": sha, "subject": subject, "files": [f for f in files if f]})
    return {"workspace": ws, "branch": s.get("branch"), "base": base, "head": s.get("head"), "commits": commits,
            "stat": W._git(path, "diff", "--stat", f"{base}..HEAD") or "", "uncommitted": bool(s.get("dirty") or s.get("untracked")),
            "note": "Only committed work can be promoted. Commit what you want to keep first." if s.get("dirty") or s.get("untracked") else None}


def prepare(p, ws: str, *, commits: Optional[List[str]] = None, paths: Optional[List[str]] = None, title: Optional[str] = None,
            reason: Optional[str] = None, auto: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not p.location.linked:
        raise ResearchError("Promotion needs shared-ledger mode and experiment workspaces (`research ledger enable`).")
    if bool(commits) == bool(paths):
        raise ResearchError("Select either --commits or --paths (exactly one).")
    s = W.show(p, ws)
    if not s["present"]:
        raise ResearchError(f"{ws} has no directory on this machine")
    top, main = _top(p), _main_branch(p)
    if not W._git(top, "rev-parse", "--verify", "-q", f"refs/heads/{main}"):
        raise ResearchError(f"Branch {main!r} does not exist", hint="Set promotion.main_branch.")
    ws_head = s["head"]
    main_head = W._git(top, "rev-parse", main)
    pids = [r["promotion"] for r in _records(p)]
    n = max([int(x.split("-")[-1]) for x in pids] + [0]) + 1
    pid = f"PROMO-{n:03d}"
    slug = re.sub(r"[^a-z0-9]+", "-", (title or s.get("experiment") or ws).lower()).strip("-")[:32]
    branch = f"promote/{pid}-{slug}"
    wt = os.path.join(os.path.dirname(top), f"{os.path.basename(top)}-promotions", pid)
    if os.path.exists(wt):
        raise ResearchError(f"{wt} already exists")
    sel: Dict[str, Any] = {}
    if commits:
        full = []
        own = set((W._git(s["path"], "rev-list", f"{s['base_rev']}..{ws_head}") or "").split())
        for c in commits:
            sha = W._git(s["path"], "rev-parse", "--verify", f"{c}^{{commit}}")
            if not sha or sha not in own:
                raise ResearchError(f"{c} is not a commit of {ws}")
            full.append(sha)
        sel["commits"] = full
    else:
        for pth in paths or []:
            if os.path.isabs(pth) or ".." in pth.replace("\\", "/").split("/") or pth.startswith(".research"):
                raise ResearchError(f"Unsafe path {pth!r}")
            if not W._git(s["path"], "ls-tree", "-r", "--name-only", ws_head, "--", pth):
                raise ResearchError(f"{pth} is not in the committed workspace head")
        sel["paths"] = list(paths or [])
    _git(top, "worktree", "add", "-q", "-b", branch, wt, main_head)
    try:
        if commits:
            for sha in sel["commits"]:
                _git(wt, "cherry-pick", "--allow-empty", "-x", sha)
        else:
            _git(wt, "checkout", ws_head, "--", *sel["paths"])
            if W._git(wt, "status", "--porcelain"):
                _git(wt, "commit", "-q", "-m", f"Promote selected files from {s.get('branch')} ({pid})\n\nPaths: " + ", ".join(sel["paths"]))
    except Exception:
        import subprocess
        subprocess.run(["git", "cherry-pick", "--abort"], cwd=wt, capture_output=True)
        subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=top, capture_output=True)
        subprocess.run(["git", "branch", "-D", branch], cwd=top, capture_output=True)   # our own just-created branch only
        raise
    head = _head(wt)
    with p.tx():
        _append(p, {"op": "prepare", "promotion": pid, "workspace": ws, "experiment": s.get("experiment"), "title": title or f"Promote from {ws}",
                    "branch": branch, "base_main": main_head, "workspace_head": ws_head, "candidate_head": head, "selection": sel,
                    "reason": reason, "source_branch": s.get("branch"), **({"auto": auto} if auto else {})})
        p.event("promotion", pid, "prepared", f"{pid} prepared from {ws} on {branch}")
    W._remember_path(W.common_dir(top), pid, wt)
    return show(p, pid)


def diff(p, pid: str, max_bytes: int = 400_000) -> Dict[str, Any]:
    c = show(p, pid)
    if not c["present"]:
        raise ResearchError(f"{pid}'s worktree is missing on this machine")
    main = _main_branch(p)
    patch = W._git(c["path"], "diff", f"{main}...HEAD") or ""
    stat = W._git(c["path"], "diff", "--stat", f"{main}...HEAD") or ""
    return {"promotion": pid, "stat": stat, "patch": patch[:max_bytes], "truncated": len(patch) > max_bytes}


# ------------------------------------------------------------------------------------------------ gates
def verify(p, pid: str, command: Optional[str] = None) -> Dict[str, Any]:
    from .processes import shell_check
    c = show(p, pid)
    if c["state"] in ("promoted", "abandoned") or not c["present"]:
        raise ResearchError(f"{pid} cannot be verified (state {c['state']}, worktree {'present' if c['present'] else 'missing'})")
    cmd = command or _cfg(p).get("test_command")
    if not cmd:
        raise ResearchError("No test command configured", hint="Set promotion.test_command (Settings → Git & workspaces) or pass --command.")
    if W._git(c["path"], "status", "--porcelain"):
        raise ResearchError(f"{pid}'s worktree has uncommitted changes, so a test run would not match any commit",
                            hint="Commit them in the candidate worktree (or discard them), then verify again.")
    head = _head(c["path"])
    r = shell_check(cmd, cwd=c["path"], env={**os.environ, "RESEARCH_PROMOTION": pid}, timeout=int(_cfg(p).get("test_timeout_seconds") or 1800), output_limit=3000)
    with p.tx():
        _append(p, {"op": "verify", "promotion": pid, "head": head, "command": cmd, "ok": r["ok"], "exit_code": r["exit_code"],
                    "seconds": r["seconds"], "timed_out": r["timed_out"], "output": r["output"]})
        p.event("promotion", pid, "verified" if r["ok"] else "verification_failed", f"{pid}: `{cmd}` exit {r['exit_code']} at {head[:8]}")
    return show(p, pid)


def review(p, pid: str, verdict: str, note: Optional[str] = None) -> Dict[str, Any]:
    c = show(p, pid)
    verdict = verdict.lower()
    if verdict not in ("approve", "changes", "comment"):
        raise ResearchError("verdict must be approve, changes or comment")
    if verdict == "approve" and (p.author.get("author_name") == c.get("prepared_by") and p.author.get("author_type") == c.get("prepared_by_type")):
        raise PolicyBlocked("The preparer cannot review their own promotion", hint="Ask a different person or agent for an independent review.")
    if verdict == "approve" and c.get("auto") and p.author.get("author_name") in (c["auto"].get("implementers") or []):
        raise PolicyBlocked("The agent that implemented this work cannot review it", hint="Use a different reviewer profile (auto_promotion.reviewer_profile).")
    if verdict == "approve" and c["gates"]["tests"]["state"] != "passed":
        raise PolicyBlocked("Review approval needs passing tests for the current candidate", hint="`research promote verify` first.")
    with p.tx():
        _append(p, {"op": "review", "promotion": pid, "head": c["gates"]["head"], "verdict": verdict, "note": note})
        p.event("promotion", pid, "reviewed", f"{pid}: {verdict}" + (f" — {note}" if note else ""))
    return show(p, pid)


def approve(p, pid: str, note: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
    if not _is_human(p):
        raise PolicyBlocked("Only a human can approve a promotion into main", hint="Ask the researcher to run `research promote approve` or use the Work page.")
    from . import githygiene as GH
    pf = GH.require_preflight(p, "promotion")      # before anything else: uncommitted work in main is reported (or, with git.preflight=block, refused) first
    c = show(p, pid)
    g = c["gates"]
    open_ = [k for k, ok in (("tests passing", g["tests"]["state"] == "passed"), ("independent review", g["review"]["state"] == "approved"),
                             ("scientific evidence", g["evidence"]["state"] in ("passed", "info")),
                             ("scientific content provenance: " + g["scientific_content"].get("detail", ""), g["scientific_content"]["state"] != "failed"))
             if not ok]
    if open_:
        raise PolicyBlocked("Cannot approve yet; open gates: " + ", ".join(open_), hint="Each gate is shown by `research promote show`.")
    from . import approval
    approval.confirm(p, f"Approve promotion {pid}", note, pin, rationale_required=False)
    with p.tx():
        _append(p, {"op": "approve", "promotion": pid, "head": g["head"], "note": note})
        p.event("promotion", pid, "approved", f"{pid} approved at {g['head'][:8]} by {p.author.get('author_name')}")
    out = show(p, pid)
    if pf.get("issues"):
        out["git_preflight"] = pf
    return out


def _ff_merge(p, c: Dict[str, Any], strict: bool = False) -> Dict[str, str]:
    """The only code that moves `main`: `git merge --ff-only`. Anything unexpected is refused with nothing changed.
    `strict` (automatic promotion): main must be exactly the revision the candidate was prepared and tested on."""
    main = _main_branch(p)
    top = _top(p)
    mw = _main_worktree(p)
    if not mw:
        raise ResearchError(f"{main} is not checked out in any worktree; check it out where you want it merged, then retry.")
    if W._git(mw["path"], "status", "--porcelain", "--untracked-files=no"):
        raise ResearchError(f"The {main} worktree at {mw['path']} has uncommitted changes; nothing was merged.")
    cur_main = W._git(top, "rev-parse", main)
    head = c["gates"]["head"]
    if cur_main != c["base_main"] and (strict or not _is_ancestor(mw["path"], cur_main, head)):
        raise ResearchError(f"{main} moved since this candidate was prepared; prepare a new candidate from the current {main}.")
    import subprocess
    r = subprocess.run(["git", "merge", "--ff-only", c["branch"]], cwd=mw["path"], capture_output=True, text=True)
    if r.returncode != 0:
        raise ResearchError("Fast-forward merge was refused by git: " + (r.stderr or r.stdout).strip()[-300:] +
                            f" Nothing changed. Prepare a new candidate from the current {main}.")
    return {"commit": W._git(top, "rev-parse", main), "main_before": cur_main, "head": head}


def integrate(p, pid: str, confirm: bool = False, pin: Optional[str] = None) -> Dict[str, Any]:
    """Human-confirmed, fast-forward-only merge. Anything else is refused."""
    if not _is_human(p):
        raise PolicyBlocked("Only a human can integrate into main")
    if not confirm:
        raise PolicyBlocked("Integration changes main; repeat with --confirm after reading `research promote diff`")
    c = show(p, pid)
    if c["state"] != "approved" or not c["classification"]["integration_ready"]:   # before the PIN: a refused target is not an approval
        raise PolicyBlocked(f"{pid} is {c['state']}, not approved with every gate passing")
    from . import approval
    approval.confirm(p, f"Merge {pid} into main", None, pin, rationale_required=False)
    m = _ff_merge(p, c)
    with p.tx():
        _append(p, {"op": "integrate", "promotion": pid, "commit": m["commit"], "main_before": m["main_before"], "head": m["head"],
                    "evidence": c["gates"]["evidence"].get("findings"), "experiment": c.get("experiment")})
        p.event("promotion", pid, "promoted", f"{pid} merged into {_main_branch(p)} at {m['commit'][:8]}")
    return show(p, pid)


def supervisor_approve(p, pid: str) -> Dict[str, Any]:
    """Technical approval of CODE under a human plan authorization. Recorded as the supervisor (system), never as a person, and
    only when tests and an independent review already passed. It says nothing about the science."""
    c = show(p, pid)
    auth = _require_supervisor(p, c)
    g = c["gates"]
    if g["tests"]["state"] != "passed" or g["review"]["state"] != "approved":
        raise PolicyBlocked(f"{pid}: tests and an independent review must pass first")
    with p.tx():
        _append(p, {"op": "approve", "promotion": pid, "head": g["head"], "mode": "supervisor_authorized", "authorized_by": auth.get("by"),
                    "note": "code only; no scientific claim is approved"})
        p.event("promotion", pid, "approved", f"{pid} code-approved at {g['head'][:8]} under {auth.get('by')}'s plan authorization")
    return show(p, pid)


def integrate_authorized(p, pid: str) -> Dict[str, Any]:
    c = show(p, pid)
    auth = _require_supervisor(p, c)
    if c["state"] != "approved" or not c["classification"]["integration_ready"] or (c.get("approval") or {}).get("mode") != "supervisor_authorized":
        raise PolicyBlocked(f"{pid} is {c['state']}, not approved with every gate passing")
    m = _ff_merge(p, c, strict=True)
    with p.tx():
        _append(p, {"op": "integrate", "promotion": pid, "commit": m["commit"], "main_before": m["main_before"], "head": m["head"],
                    "mode": "supervisor_authorized", "authorized_by": auth.get("by"), "experiment": c.get("experiment")})
        p.event("promotion", pid, "promoted", f"{pid} fast-forwarded into {_main_branch(p)} at {m['commit'][:8]} (authorized by {auth.get('by')})")
    return show(p, pid)


def abandon(p, pid: str, reason: Optional[str] = None) -> Dict[str, Any]:
    c = show(p, pid)
    if c["state"] == "promoted":
        raise ResearchError(f"{pid} is already promoted")
    with p.tx():
        _append(p, {"op": "abandon", "promotion": pid, "reason": reason})
        p.event("promotion", pid, "abandoned", f"{pid} abandoned" + (f": {reason}" if reason else ""))
    return show(p, pid)
