"""Opt-in, unattended promotion of verified CODE into local `main` (Research OS 2.4).

Off by default. It runs only when ALL of these hold: the plan YAML declares `execution.auto_promotion: true` (reviewable text), a HUMAN has
authorized it for this plan run with `research plan autopromote authorize` (PIN-gated, stored in the run state, bound to a fingerprint of
the verification policy), and the policy still matches that fingerprint. An agent can neither declare-and-authorize, nor change the policy
without suspending it. Manual mode (the default) is not touched by anything here.

It reuses the promotion-candidate ledger (`promotion.py`) and adds only the supervisor-authorized path:

    pending verified commits (supervisor-made, per experiment workspace; already-integrated and no-op work excluded)
      -> trigger: experiment boundary (every task of the experiment verified) or a downstream task that depends on that code
      -> candidate from CURRENT main, cherry-pick of only the unpromoted commits, guards (no edits to existing tests, no large files)
      -> integration tests, then numerical checks (the configured commands; never rewritten here)
      -> independent review by a verifier that is not the implementer (an agent run, or a human via `research promote review`)
      -> supervisor code-approval (recorded as `system/supervisor`, naming the authorizing human) -> `git merge --ff-only` into main
      -> dependents unblock; their worktrees start from (or are fast-forwarded to) the integrated main

It never judges science: findings and hypotheses are untouched; a negative result can still promote valid code. It never forces, rebases,
resets, pushes, retries a failed merge blindly, deletes branches/worktrees/untracked files, or edits verification commands. Any problem
pauses that item (fail closed) with the error persisted; a human retries or abandons it.
"""
from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import subprocess
import time
from typing import Any, Dict, Iterator, List, Optional

from . import promotion as PR, registry, workspace as W
from .util import PolicyBlocked, ResearchError, now_iso

TERMINAL = ("integrated", "noop", "cancelled")


# ----------------------------------------------------------------------------------------------- policy & authorization
def policy(p) -> Dict[str, Any]:
    """Canonical test command: `promotion.test_command` (the one Settings → Git & workspaces shows, used by manual and automatic promotion).
    `auto_promotion.test_command` is a legacy alias, honoured only when the canonical one is empty; if both are set and differ that is a
    configuration conflict (see `conflicts`). Everything else is under `auto_promotion`."""
    c = registry.section(p, "auto_promotion")
    canon, legacy = (p.config.get("promotion") or {}).get("test_command"), c["test_command"]
    c["test_command"] = canon or legacy
    c["test_command_source"] = "promotion.test_command" if canon else "auto_promotion.test_command (legacy)" if legacy else None
    c["conflicts"] = [f"promotion.test_command ({canon!r}) and auto_promotion.test_command ({legacy!r}) differ; keep only promotion.test_command"] if canon and legacy and canon != legacy else []
    return c


def fingerprint(p) -> str:
    c = policy(p)
    keys = ("test_command", "numerical_command", "reviewer_profile", "require_distinct_reviewer", "protected_test_globs", "max_file_mb")
    blob = json.dumps({**{k: c[k] for k in keys}, "main": PR._main_branch(p)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _state(rs: Dict[str, Any]) -> Dict[str, Any]:
    return rs.setdefault("autopromote", {"authorized": None, "items": [], "integrated": {}})


def declared(st: Dict[str, Any]) -> bool:
    return bool(((st.get("spec") or {}).get("execution") or {}).get("auto_promotion"))


def mode(p, st, rs) -> Dict[str, Any]:
    """`manual` (default) | `active` | `suspended` (declared and authorized but the policy changed: fail closed)."""
    a = _state(rs)
    if not declared(st) or not a["authorized"]:
        return {"mode": "manual", "declared": declared(st), "authorized": bool(a["authorized"])}
    if a["authorized"]["fingerprint"] != fingerprint(p):
        return {"mode": "suspended", "why": "verification policy changed since authorization; re-authorize", "declared": True, "authorized": True}
    return {"mode": "active", "declared": True, "authorized": True, "by": a["authorized"]["by"]}


def check_ready(p, st) -> None:
    c = policy(p)
    if not declared(st):
        raise ResearchError("The plan does not declare execution.auto_promotion: true", hint="Add it to the plan YAML, review the diff, apply it.")
    if not p.location.linked:
        raise ResearchError("Automatic promotion needs shared-ledger mode and experiment workspaces")
    if c["conflicts"]:
        raise ResearchError("Conflicting test commands: " + c["conflicts"][0], hint="Delete auto_promotion.test_command; `promotion.test_command` is the canonical setting.")
    if not c["test_command"]:
        raise ResearchError("promotion.test_command is required (Settings → Git & workspaces, or `research config set promotion.test_command …`)")
    if not c["reviewer_profile"] or c["reviewer_profile"] not in ((p.config.get("agents") or {}).get("profiles") or {}):
        raise ResearchError("auto_promotion.reviewer_profile must name a configured agent profile (an independent reviewer is mandatory)")
    if not W._git(W.toplevel(p.root) or p.root, "rev-parse", "--verify", "-q", f"refs/heads/{PR._main_branch(p)}"):
        raise ResearchError(f"Branch {PR._main_branch(p)!r} does not exist")


# ----------------------------------------------------------------------------------------------- git helpers
def _top(p) -> str:
    return PR._top(p)


def _main_sha(p) -> str:
    return W._git_ok(_top(p), "rev-parse", PR._main_branch(p))


@contextlib.contextmanager
def _as_supervisor(p, authorization: Optional[Dict[str, Any]] = None) -> Iterator[None]:
    human = p.author
    p.author = {"author_type": "system", "author_name": "supervisor", "author_model": None}
    try:
        with (PR.supervisor_authority(authorization) if authorization else contextlib.nullcontext()):
            yield
    finally:
        p.author = human


def _integrated(p, rs: Dict[str, Any], sha: str) -> bool:
    return sha in _state(rs)["integrated"] or PR._is_ancestor(_top(p), sha, PR._main_branch(p))


def pending(p, st: Dict[str, Any], rs: Dict[str, Any]) -> Dict[tuple, List[Dict[str, Any]]]:
    """Verified supervisor commits not yet in main, keyed by (experiment, workspace). No commit exists for a no-op task, so none is listed."""
    out: Dict[tuple, List[Dict[str, Any]]] = {}
    for tid, e in (rs.get("tasks") or {}).items():
        if not p.exists("task", tid):
            continue
        exp = p.get("task", tid).get("related_experiment_id")
        for a in e.get("attempts", []):
            sha = a.get("commit")
            if not sha or a.get("state") != "verified" or a.get("voided") or _integrated(p, rs, sha):
                continue
            ws = (a.get("workspace") or {}).get("id") or (W.for_task(p, tid) or {}).get("id")
            if exp and ws:
                out.setdefault((exp, ws), []).append({"sha": sha, "task": tid, "profile": a.get("profile")})
    return out


def _complete(p, st: Dict[str, Any], rs: Dict[str, Any], exp: str) -> bool:
    """Experiment boundary: every live task of the experiment is verified."""
    live = [tid for k, tid in st["tasks"].items() if k not in (st.get("retired") or {}) and p.exists("task", tid)
            and p.get("task", tid).get("related_experiment_id") == exp]
    return bool(live) and all((rs["tasks"].get(t) or {}).get("phase") == "verified" for t in live)


def _open(rs: Dict[str, Any], exp: Optional[str] = None) -> List[Dict[str, Any]]:
    return [i for i in _state(rs)["items"] if i["status"] not in TERMINAL and (exp is None or i["experiment"] == exp)]


def _enqueue(rs: Dict[str, Any], exp: str, ws: str, why: str) -> Dict[str, Any]:
    a = _state(rs)
    for i in _open(rs, exp):
        if i["workspace"] == ws:
            return i
    item = {"id": f"AP-{len(a['items']) + 1:03d}", "experiment": exp, "workspace": ws, "trigger": why, "status": "queued", "sources": [],
            "promotion": None, "base_main": None, "candidate_head": None, "main_after": None, "tests": None, "numerical": None,
            "review": None, "error": None, "created": now_iso(), "history": []}
    a["items"].append(item)
    return item


def _set(item: Dict[str, Any], status: str, **kw: Any) -> None:
    item.update(kw, status=status, updated=now_iso())
    item["history"].append({"ts": now_iso(), "status": status, **({"error": kw["error"]} if kw.get("error") else {})})


def _pause(item: Dict[str, Any], why: str, out: Dict[str, Any]) -> None:
    _set(item, "paused", error=why)
    out["events"].append(f"automatic promotion {item['id']} ({item['experiment']}) paused: {why}")


# ----------------------------------------------------------------------------------------------- scheduling hooks
def gate(p, st: Dict[str, Any], rs: Dict[str, Any], tid: str) -> Optional[str]:
    """Called by the scheduler before a launch. Returns why this task must wait, or None. Manual mode always returns None."""
    m = mode(p, st, rs)
    if m["mode"] == "manual":
        return None
    own = p.get("task", tid).get("related_experiment_id")
    ups, seen, todo = set(), set(), list(p.get("task", tid).get("depends_on") or [])
    while todo:                                           # transitive: a code dependency may sit several tasks back
        d = todo.pop()
        if d in seen or not p.exists("task", d):
            continue
        seen.add(d)
        e = p.get("task", d).get("related_experiment_id")
        if e and e != own:
            ups.add(e)
        todo += p.get("task", d).get("depends_on") or []
    if not ups:
        return None
    if m["mode"] == "suspended":
        return f"automatic promotion is suspended ({m['why']})"
    pend = pending(p, st, rs)
    for exp in sorted(ups):
        for (e, ws), _ in sorted(pend.items()):
            if e == exp:
                it = _enqueue(rs, exp, ws, "dependency")
                return f"waiting for {exp}'s verified code to be integrated into main ({it['id']}: {it['status']})"
        for it in _open(rs, exp):
            return f"waiting for {exp}'s code promotion {it['id']} ({it['status']})"
    return _sync_workspace(p, rs, own, tid)


def _sync_workspace(p, rs: Dict[str, Any], exp: Optional[str], tid: str) -> Optional[str]:
    """A downstream experiment must work on the integrated main: new worktrees start from its SHA; an existing one with no work of its
    own is fast-forwarded (never rebased); one that diverged is refused."""
    if not exp:
        return None
    main = _main_sha(p)
    try:
        ws = W.ensure_for_experiment(p, exp, base=main)
        path, head = ws["path"], W._git_ok(ws["path"], "rev-parse", "HEAD")
        if not PR._is_ancestor(path, main, head):
            if W._git(path, "status", "--porcelain", "--untracked-files=no"):
                return f"{exp}'s worktree has uncommitted changes, so main cannot be brought in; commit or discard them"
            if PR._is_ancestor(path, head, main):
                W._git_ok(path, "merge", "--ff-only", main)
            else:   # its own (already cherry-picked) commits: a recorded merge of main INTO the experiment branch; never a rebase, never main
                env = {**os.environ, "GIT_AUTHOR_NAME": "Research OS supervisor", "GIT_AUTHOR_EMAIL": "supervisor@research-os.local",
                       "GIT_COMMITTER_NAME": "Research OS supervisor", "GIT_COMMITTER_EMAIL": "supervisor@research-os.local"}
                r = subprocess.run(["git", "merge", "--no-edit", main], cwd=path, capture_output=True, text=True, env=env)
                if r.returncode:
                    subprocess.run(["git", "merge", "--abort"], cwd=path, capture_output=True)
                    return f"merging {PR._main_branch(p)} into {exp}'s branch conflicts; aborted, nothing changed. Resolve it yourself, then resume"
                _state(rs).setdefault("syncs", []).append({"ts": now_iso(), "experiment": exp, "task": tid, "main": main})
    except ResearchError as ex:
        return f"could not prepare {exp}'s worktree from main: {ex}"
    _state(rs).setdefault("launch_bases", {})[tid] = main
    return None


def settled(p, st: Dict[str, Any], rs: Dict[str, Any]) -> bool:
    if mode(p, st, rs)["mode"] != "active":
        return True
    return not _open(rs) and not pending(p, st, rs)


def step(p, st: Dict[str, Any], rs: Dict[str, Any], c: Dict[str, Any], out: Dict[str, Any]) -> None:
    """One bounded transition per tick, strictly one promotion at a time and in order; a paused item holds the queue (fail closed)."""
    m = mode(p, st, rs)
    if m["mode"] != "active":
        return
    for (exp, ws), _ in sorted(pending(p, st, rs).items()):
        if _complete(p, st, rs, exp):
            _enqueue(rs, exp, ws, "experiment_complete")
    item = next(iter(_open(rs)), None)
    if not item or item["status"] == "paused":
        return
    try:
        _advance(p, st, rs, item, out)
    except (PolicyBlocked, ResearchError) as ex:
        _pause(item, str(ex), out)
    except Exception as ex:   # never crash the supervisor, never continue past the unknown
        _pause(item, f"{type(ex).__name__}: {ex}", out)


# ----------------------------------------------------------------------------------------------- the state machine
def _advance(p, st, rs, item, out) -> None:
    from .supervisor import save_run
    pol, auth = policy(p), _state(rs)["authorized"]
    s = item["status"]
    if s == "queued":
        check_ready(p, st)
        _prepare(p, st, rs, item, pol, out)
    elif s == "prepared":
        _verify(p, item, pol, out)
    elif s in ("verified", "awaiting_review"):
        _review(p, item, pol, out)
    elif s == "reviewed":
        with _as_supervisor(p, auth):
            PR.supervisor_approve(p, item["promotion"])
        _set(item, "approved")
    elif s == "approved":
        _integrate(p, rs, item, auth, out)
    save_run(p, rs)


def _prepare(p, st, rs, item, pol, out) -> None:
    pend = [x for x in pending(p, st, rs).get((item["experiment"], item["workspace"]), [])]
    if not pend:
        return _set(item, "noop", note="nothing unpromoted remains")
    ws = W.show(p, item["workspace"])
    order = (W._git_ok(ws["path"], "rev-list", "--reverse", f"{ws['base_rev']}..HEAD")).split()
    shas = sorted({x["sha"] for x in pend}, key=lambda s: order.index(s) if s in order else len(order))
    marker = f"autopromote:{st['plan_key']}:{item['id']}:{item.get('attempt', 1)}"
    prior = next((c for c in PR.list_candidates(p) if c.get("reason") == marker), None)   # restart: adopt the open candidate, never duplicate
    with _as_supervisor(p):
        c = prior or PR.prepare(p, item["workspace"], commits=shas, title=f"{st['plan_key']} {item['experiment']} (automatic)", reason=marker,
                                auto={"plan": st["plan_key"], "item": item["id"], "implementers": sorted({x["profile"] for x in pend if x.get("profile")}),
                                      "sources": shas})
    item.update(promotion=c["id"], base_main=c["base_main"], candidate_head=c["gates"]["head"], sources=[x for x in pend])
    main = PR._main_branch(p)
    if W._git(c["path"], "diff", "--quiet", main, c["gates"]["head"]) is not None:       # identical to main: the work is already there
        with _as_supervisor(p):
            PR.abandon(p, c["id"], "no change relative to main")
        for x in pend:
            _state(rs)["integrated"][x["sha"]] = {"item": item["id"], "noop": True}
        return _set(item, "noop", note="candidate identical to main")
    _guard_diff(c["path"], main, c["gates"]["head"], pol)
    _set(item, "prepared", error=None)


def _guard_diff(path: str, main: str, head: str, pol: Dict[str, Any]) -> None:
    """Verification tests are frozen: existing tests may not be edited or deleted by promoted work; bulky files stay out of code history."""
    for line in W._git_ok(path, "diff", "--name-status", "-M", main, head).splitlines():
        parts = line.split("\t")
        status, files = parts[0], parts[1:]
        for f in files:
            if f.startswith(".research"):
                raise PolicyBlocked(f"{f}: ledger files are never promoted")
            if status[0] in "MDRT" and any(fnmatch.fnmatch(f, g) for g in pol["protected_test_globs"]):
                raise PolicyBlocked(f"{f}: promoted work may not modify or delete an existing test ({status}); a human must review that change")
            if status[0] in "AMR":
                size = int(W._git(path, "cat-file", "-s", f"{head}:{f}") or 0)
                if size > float(pol["max_file_mb"]) * 1_000_000:
                    raise PolicyBlocked(f"{f}: {size // 1_000_000} MB exceeds auto_promotion.max_file_mb; datasets and results stay out of main")


def _verify(p, item, pol, out) -> None:
    with _as_supervisor(p):
        r = PR.verify(p, item["promotion"], pol["test_command"])
    item["tests"] = {"state": r["gates"]["tests"]["state"], "command": pol["test_command"], "head": r["gates"]["head"], "at": now_iso()}
    if r["gates"]["tests"]["state"] != "passed":
        return _pause(item, f"integration tests failed: {(r['gates']['tests'].get('output') or '')[-300:]}", out)
    if pol["numerical_command"]:
        with _as_supervisor(p):
            r = PR.verify(p, item["promotion"], pol["numerical_command"])
        item["numerical"] = {"state": r["gates"]["tests"]["state"], "command": pol["numerical_command"], "at": now_iso()}
        if r["gates"]["tests"]["state"] != "passed":
            return _pause(item, f"numerical checks failed: {(r['gates']['tests'].get('output') or '')[-300:]}", out)
    else:
        item["numerical"] = {"state": "not_configured"}
    _set(item, "verified", error=None)


def _review(p, item, pol, out) -> None:
    g = PR.show(p, item["promotion"])["gates"]
    if g["head"] != item["candidate_head"]:
        return _pause(item, "the candidate changed after it was verified", out)
    rv = g["review"]
    if rv["state"] == "approved":
        item["review"] = {"by": rv.get("by"), "at": now_iso()}
        return _set(item, "reviewed")
    if rv["state"] == "changes_requested":
        return _pause(item, f"reviewer requested changes: {rv.get('note')}", out)
    if not item.get("review_requested"):
        item["review_requested"] = _launch_reviewer(p, item, pol)
        return _set(item, "awaiting_review")
    if time.time() - item["review_requested"]["epoch"] > float(pol["review_timeout_minutes"]) * 60:
        _pause(item, "no independent review arrived in time", out)


def _launch_reviewer(p, item, pol) -> Dict[str, Any]:
    """Start the independent reviewer: a fresh headless verifier on a different profile from the implementers. It records its verdict with
    `research promote review`; the preparer (supervisor) and the implementing profiles are refused by promotion.review."""
    from . import agents
    cand = PR.show(p, item["promotion"])
    if pol["require_distinct_reviewer"] and pol["reviewer_profile"] in (cand.get("auto") or {}).get("implementers", []):
        raise PolicyBlocked(f"reviewer profile {pol['reviewer_profile']!r} also implemented this work; configure a different "
                            "auto_promotion.reviewer_profile (or set require_distinct_reviewer: false deliberately)")
    text = (f"Independently review promotion candidate {cand['id']} for CODE correctness only. Worktree: {cand['path']}. Read `git -C {cand['path']} diff "
            f"{PR._main_branch(p)}...HEAD`. Check correctness, that no existing test was weakened, and that nothing unrelated is included. Do NOT judge "
            f"the scientific result or hypothesis; a negative result with correct code is fine. Then run exactly one of: "
            f"`research promote review {cand['id']} --verdict approve --note \"...\"` or `--verdict changes --note \"...\"`. Do not edit files.")
    d = agents.dispatch(p, role="verifier", profile=pol["reviewer_profile"], instructions=text, headless=True)
    if not d.get("available"):
        raise ResearchError(d.get("problem") or "reviewer provider not available")
    proc = subprocess.Popen(d["argv"], cwd=cand["path"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=os.name == "posix", env={**os.environ, **(d.get("env") or {})})
    return {"at": now_iso(), "epoch": time.time(), "profile": d.get("profile"), "pid": proc.pid}


def _integrate(p, rs, item, auth, out) -> None:
    from .supervisor import save_run
    c = PR.show(p, item["promotion"])
    if c["state"] != "promoted":
        if PR._is_ancestor(_top(p), c["gates"]["head"], PR._main_branch(p)):   # merged but we crashed before recording it
            raise ResearchError(f"{c['id']}'s commit is already in main without an integration record; a human should inspect and abandon it")
        with _as_supervisor(p, auth):
            c = PR.integrate_authorized(p, item["promotion"])
    commit = c["integration"]["commit"]
    for x in item["sources"]:
        _state(rs)["integrated"][x["sha"]] = {"item": item["id"], "commit": commit}
    _set(item, "integrated", main_after=commit, error=None)
    save_run(p, rs)
    _manifest(p, item, c)


def _manifest(p, item, c) -> None:
    """Provenance next to the run state: source/candidate/main SHAs and where the experiment's (uncommitted) results and figures live, so the
    Quarto guide and reports keep resolving them. Results are never copied into main."""
    ws = W.show(p, item["workspace"])
    figs = []
    for root, _, files in os.walk(os.path.join(ws["path"], "outputs")):
        figs += [os.path.relpath(os.path.join(root, f), ws["path"]) for f in files if f.lower().endswith((".png", ".svg", ".pdf"))]
    d = os.path.join(p.path("supervisor"), "promotions")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, item["id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump({**item, "workspace_path": ws["path"], "figures": sorted(figs)[:200], "integration": c.get("integration")}, f, indent=1, sort_keys=True, default=str)


# ----------------------------------------------------------------------------------------------- human controls (called from supervisor.py)
def authorize(p, st, rs, note: Optional[str]) -> Dict[str, Any]:
    """Record the human's authorization (the caller has already passed the PIN gate) bound to the current verification policy."""
    check_ready(p, st)
    by = (p.author or {}).get("author_name") or os.environ.get("RESEARCH_AUTHOR") or "human"
    _state(rs)["authorized"] = {"by": by, "ts": now_iso(), "note": note, "fingerprint": fingerprint(p), "plan": st["plan_key"]}
    rs.setdefault("history", []).append({"ts": now_iso(), "event": "autopromote_authorized", "by": by})
    return summary(p, st, rs)


def revoke(p, st, rs) -> Dict[str, Any]:
    _state(rs)["authorized"] = None
    rs.setdefault("history", []).append({"ts": now_iso(), "event": "autopromote_revoked"})
    return summary(p, st, rs)


def retry(p, st, rs, item_id: str) -> Dict[str, Any]:
    """A human re-queues a paused item: its candidate is abandoned (kept on disk) and a new one is prepared from the CURRENT main and re-tested."""
    item = next((i for i in _state(rs)["items"] if i["id"] == item_id), None)
    if not item or item["status"] != "paused":
        raise ResearchError(f"{item_id} is not a paused automatic promotion")
    if item.get("promotion") and PR.show(p, item["promotion"])["state"] not in ("promoted", "abandoned"):
        with _as_supervisor(p):
            PR.abandon(p, item["promotion"], f"retry of {item_id} by a human")
    item.update(promotion=None, review_requested=None, tests=None, numerical=None, review=None, candidate_head=None)
    item["attempt"] = item.get("attempt", 1) + 1
    _set(item, "queued", error=None)
    return summary(p, st, rs)


def summary(p, st, rs) -> Dict[str, Any]:
    a = _state(rs)
    pol = policy(p)
    return {**mode(p, st, rs), "test_command": pol["test_command"], "test_command_source": pol["test_command_source"], "conflicts": pol["conflicts"],
            "authorization": a["authorized"], "items": a["items"],
            "pending": [{"experiment": e, "workspace": w, "commits": [x["sha"][:10] for x in v]} for (e, w), v in sorted(pending(p, st, rs).items())]}
