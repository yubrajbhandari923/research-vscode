"""The plan file: one YAML file per plan, in the repository, that you edit and Research OS follows.

Before this module a plan lived only in the ledger. Changing one number meant `plan yaml export`, an edit, `plan yaml apply`, and then a separate command to
unblock whatever had stopped. Now every plan has a tracked file (`plans/<key>.yaml` by default) and three rules:

* You edit the file. The supervisor notices on its next pass (`step`) and applies it: operational changes (budgets, attempts, prompts, profiles, resources,
  concurrency) by itself; scientific ones (new or removed tasks and experiments, acceptance, checks, datasets, dependencies, hypotheses, policy) wait for ONE
  `research plan sync`, because a file in the repository can also be written by an agent. `supervisor.plan_files.auto_sync` changes that (operational | all | off).
* Research OS edits the ledger (a repair task from self-healing, an approved intervention, `research bump`). The file is regenerated so it never lies.
* Both at once is a merge, not a conflict: your edit is applied as a patch (nothing is retired), then the file is regenerated with both sides in it.

An invalid file changes nothing: the problems are listed in `plan status` and one chat alert, and the plan keeps running on the last good revision.
`set_values` and `bump` are the same mechanism without an editor: they patch the ledger and regenerate the file.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import planspec as PS, registry, yamlio
from .store import Project, _atomic_write
from .util import ResearchError, now_iso

HEADER = ("# Research OS plan file. This file IS the plan: edit it and save.\n"
          "#   research plan status {key}     what is running, what is stuck and why\n"
          "#   research plan sync {key}       apply an edit now (the supervisor also picks edits up by itself)\n"
          "#   research bump T-ID            a task ran out of attempts: allow more and retry\n"
          "# `id:` lines tie entries to the ledger; leave them. Finished tasks cannot be redefined: add a new task instead.\n")
POLICY_EXEC = {"auto_promotion", "require_approval_for_scientific_changes", "mode"}      # execution keys that change who decides, not how much runs
SHORT = {"max_attempts": "execution.max_attempts", "requires_approval": "execution.requires_approval", "timeout_minutes": "execution.timeout_minutes",
         "workspace": "execution.workspace", "profile": "agent.profile", "model": "agent.model", "role": "agent.role", "level": "agent.level",
         "session_group": "agent.session_group"}


def cfg(p: Project) -> Dict[str, Any]:
    c = dict(registry.section(p, "supervisor").get("plan_files") or {})
    c.setdefault("dir", "plans")
    c.setdefault("auto_sync", "operational")
    return c


def main_root(p: Project) -> str:
    """Plan files live in the main checkout, also when a command runs inside an experiment worktree."""
    loc = getattr(p, "location", None)
    return loc.ledger_root if loc is not None and getattr(loc, "linked", False) else p.root


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def path_of(p: Project, st: Dict[str, Any]) -> str:
    rel = (st.get("file") or {}).get("path") or os.path.join(cfg(p)["dir"], st["plan_key"] + ".yaml")
    return rel if os.path.isabs(rel) else os.path.join(main_root(p), rel)


def _rel(p: Project, path: str) -> str:
    ap = os.path.abspath(path)
    r = os.path.relpath(ap, main_root(p))
    return ap if r.startswith(os.pardir) else r.replace(os.sep, "/")


def text_for(p: Project, key: str) -> str:
    """The plan as a file: the full export without its timestamp line, so an unchanged plan always produces the same bytes."""
    body = PS.export(p, key, scope="full").split("\n", 1)[1]
    doc = yamlio.load(body)
    for pl in doc.get("plans") or []:
        pl.pop("revision", None)          # freshness is tracked by content hash, not by a number the reader would have to keep in step
        pl.pop("scope", None)
    doc["plans"] = [_ordered(pl, PLAN_ORDER) for pl in doc.get("plans") or []]
    return HEADER.format(key=key) + yamlio.dump(doc, block=True)


# The order a person reads a plan in: what it is, then what it should establish, then how it runs, then its parts.
PLAN_ORDER = ("key", "id", "title", "objective", "success_criteria", "context", "question", "skills", "execution", "experiments", "tasks")
EXP_ORDER = ("key", "id", "title", "inherits", "question", "hypothesis", "motivation", "method", "success_criteria", "expected_outcome", "stop_conditions",
             "parameters", "metrics", "expected_artifacts", "planned_runs", "notes", "tags", "tasks")
TASK_ORDER = ("key", "id", "title", "depends_on", "agent", "description", "prompt", "inputs", "datasets", "expected_artifacts", "acceptance", "checks",
              "resources", "execution")


def _ordered(d: Dict[str, Any], order: Tuple[str, ...]) -> Dict[str, Any]:
    out = {k: d[k] for k in order if k in d}
    out.update({k: v for k, v in d.items() if k not in out})
    if "experiments" in out:
        out["experiments"] = [_ordered(e, EXP_ORDER) for e in out["experiments"] or []]
    if "tasks" in out:
        out["tasks"] = [_ordered(t, TASK_ORDER) for t in out["tasks"] or []]
    return out


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def info(p: Project, key: str) -> Dict[str, Any]:
    """Where the plan's file is and how it relates to the ledger: in_sync | edited | ledger_ahead | merge | missing | untracked."""
    st = PS._state_for(p, key)
    f = st.get("file") or {}
    path = path_of(p, st)
    text = _read(path)
    out = {"plan": st["plan_key"], "plan_id": st.get("plan_id"), "path": _rel(p, path), "abspath": path, "exists": text is not None,
           "revision": st["revision"], "file_revision": f.get("revision"), "tracked": bool(f), "problem": f.get("problem"), "held": f.get("held")}
    if not f:
        out["status"] = "untracked"
    elif text is None:
        out["status"] = "missing"
    else:
        edited, ahead = _sha(text) != f.get("sha"), f.get("revision") != st["revision"]
        out["status"] = "merge" if edited and ahead else "edited" if edited else "ledger_ahead" if ahead else "in_sync"
    return out


def _record(p: Project, st: Dict[str, Any], path: str, text: str, **extra: Any) -> None:
    st = PS.load_state(p, st["plan_key"]) or st
    st["file"] = {"path": _rel(p, path), "sha": _sha(text), "revision": st["revision"], "synced_at": now_iso(), **extra}
    PS.save_state(p, st)


def write(p: Project, key: str) -> Dict[str, Any]:
    """Regenerate the file from the ledger (comments in it are not kept: the ledger is what changed)."""
    st = PS._state_for(p, key)
    path = path_of(p, st)
    text = text_for(p, st["plan_key"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_write(path, text)
    _record(p, st, path, text)
    return info(p, st["plan_key"])


def track(p: Project, key: str, path: Optional[str] = None) -> Dict[str, Any]:
    """Give a plan its file. An existing file at `path` is taken as the plan's current text only if it applies cleanly; otherwise the ledger is written out."""
    st = PS._state_for(p, key)
    if path:
        st["file"] = {**(st.get("file") or {}), "path": _rel(p, path), "sha": None, "revision": st["revision"]}
        PS.save_state(p, st)
    if _read(path_of(p, st)) is None:
        return write(p, st["plan_key"])
    r = sync(p, st["plan_key"])
    return r[0]["file"] if r else info(p, st["plan_key"])


# ----------------------------------------------------------------------------------------------- reading an edit
def _doc_for(p: Project, st: Optional[Dict[str, Any]], text: str, full: bool) -> Tuple[Dict[str, Any], str]:
    doc = PS.parse(text)
    plans = doc.get("plans")
    if not isinstance(plans, list) or len(plans) != 1 or not isinstance(plans[0], dict):
        raise PS.PlanSpecError(["a plan file holds exactly one plan (one entry under `plans:`)"])
    pl = plans[0]
    if st and pl.get("key") != st["plan_key"]:
        raise PS.PlanSpecError([f"this file belongs to plan {st['plan_key']!r} but its key is {pl.get('key')!r}; a plan's key cannot be renamed"])
    pl.pop("revision", None)
    pl["scope"] = "full" if full else "partial"
    return doc, yamlio.dump(doc, block=True)


def classify(ops: List[Dict[str, Any]]) -> List[str]:
    """Why an edit is more than operational: one line per reason. Empty = budgets, prompts, profiles, resources and the like."""
    why = []
    for o in ops:
        k, f = o["op"], set(o.get("fields") or [])
        if k in ("create_plan", "adopt_plan"):
            why.append("a new plan")
        elif k in ("create_experiment", "adopt_experiment"):
            why.append(f"new experiment {o['key']}")
        elif k in ("create_task", "adopt_task", "retire_task", "unretire_task"):
            why.append(f"{k.split('_')[0]} task {o['key']}")
        elif k == "update_plan" and f & PS.SCIENTIFIC_PLAN:
            why.append("plan " + ", ".join(sorted(f & PS.SCIENTIFIC_PLAN)))
        elif k == "update_experiment" and f & PS.SCIENTIFIC_EXP:
            why.append(f"experiment {o['key']}: " + ", ".join(sorted(f & PS.SCIENTIFIC_EXP)))
        elif k in ("update_task", "defer_task"):
            sci = f & PS.SCIENTIFIC_TASK
            ag = (o.get("fields") or {}).get("agent") if isinstance(o.get("fields"), dict) else None
            if sci:
                why.append(f"task {o['key']}: " + ", ".join(sorted(sci)))
            if isinstance(ag, dict) and o.get("_role_changed"):
                why.append(f"task {o['key']}: agent role (changes who reviews what)")
        elif k == "update_execution" and f & POLICY_EXEC:
            why.append("plan policy: " + ", ".join(sorted(f & POLICY_EXEC)))
    return why


def _mark_roles(p: Project, st: Optional[Dict[str, Any]], ops: List[Dict[str, Any]]) -> None:
    old = (st or {}).get("spec_tasks") or {}
    for o in ops:
        if o["op"] in ("update_task", "defer_task") and isinstance(o.get("fields"), dict) and "agent" in o["fields"]:
            o["_role_changed"] = ((o["fields"]["agent"] or {}).get("role") or "implementer") != ((old.get(o["key"], {}).get("agent") or {}).get("role") or "implementer")


def check(p: Project, key: str, text: Optional[str] = None) -> Dict[str, Any]:
    """What applying the file now would do. Never changes anything. Raises PlanSpecError for an invalid file."""
    st = PS._state_for(p, key)
    i = info(p, st["plan_key"])
    text = _read(i["abspath"]) if text is None else text
    if text is None:
        return {**i, "ops": [], "scientific": [], "conflicts": [], "warnings": []}
    _, norm = _doc_for(p, st, text, full=i["status"] != "merge")
    d = PS.diff(p, norm)
    ops = [o for pl in d["plans"] for o in pl["ops"]]
    _mark_roles(p, st, ops)
    return {**i, "ops": ops, "scientific": classify(ops), "conflicts": d["conflicts"], "warnings": d["warnings"], "_text": norm}


# ----------------------------------------------------------------------------------------------- applying
def sync(p: Project, key: Optional[str] = None, auto: bool = False) -> List[Dict[str, Any]]:
    """Bring file and ledger together for one plan (or all tracked plans). `auto` is the supervisor's own pass: it applies what `auto_sync` allows and
    holds the rest, recording why, instead of raising."""
    keys = [PS._state_for(p, key)["plan_key"]] if key else [s["plan_key"] for s in PS.all_states(p) if s.get("file") and not s.get("archived")]
    return [_sync_one(p, k, auto) for k in keys]


def _held(p: Project, key: str, **f: Any) -> None:
    st = PS.load_state(p, key)
    st["file"] = {**(st.get("file") or {}), **f}
    PS.save_state(p, st)


def _sync_one(p: Project, key: str, auto: bool) -> Dict[str, Any]:
    i = info(p, key)
    if i["status"] in ("untracked", "missing", "ledger_ahead"):
        return {"plan": key, "action": "written", "file": write(p, key), "operations": 0}
    if i["status"] == "in_sync":
        if i.get("problem") or i.get("held"):
            _held(p, key, problem=None, held=None)
        return {"plan": key, "action": "none", "file": info(p, key), "operations": 0}
    text = _read(i["abspath"])
    sha = _sha(text)
    try:
        c = check(p, key, text)
        if c["conflicts"]:
            raise PS.PlanSpecError(c["conflicts"])
    except ResearchError as ex:
        errs = getattr(ex, "errors", None) or [str(ex)]
        if not auto:
            raise
        st = PS.load_state(p, key)
        if (st.get("file") or {}).get("problem_sha") != sha:
            _held(p, key, problem=errs, problem_sha=sha, held=None)
            p.event("plan", st.get("plan_id"), "plan_file_invalid", f"{i['path']} was edited but cannot be applied: {errs[0][:200]}")
        return {"plan": key, "action": "invalid", "errors": errs, "file": info(p, key), "operations": 0, "fresh": (st.get("file") or {}).get("problem_sha") != sha}
    mode = cfg(p)["auto_sync"]
    if auto and (mode == "off" or (mode != "all" and c["scientific"])):
        st = PS.load_state(p, key)
        fresh = (st.get("file") or {}).get("held_sha") != sha
        if fresh:
            _held(p, key, held=c["scientific"] or ["automatic sync is off"], held_sha=sha, problem=None)
        return {"plan": key, "action": "held", "scientific": c["scientific"], "file": info(p, key), "operations": len(c["ops"]), "fresh": fresh}
    merged = i["status"] == "merge"
    r = PS.apply(p, c["_text"], source="file")
    n = sum(pl.get("operations") or 0 for pl in r["plans"])
    st = PS.load_state(p, key)
    if merged:                       # the ledger had moved underneath the edit: write both sides back
        out = write(p, key)
    else:                            # the common case: the file is exactly what was applied, so it (and its comments) is left alone
        _record(p, st, i["abspath"], text)
        out = info(p, key)
    _held(p, key, problem=None, held=None, problem_sha=None, held_sha=None)
    return {"plan": key, "action": "merged" if merged else "applied", "operations": n, "ops": c["ops"], "warnings": c["warnings"], "file": info(p, key) if merged else out,
            "revision": st["revision"]}


def add(p: Project, path: str) -> Dict[str, Any]:
    """Register a new plan from a file and track that file in place. A file for a plan that already exists is synced."""
    text = _read(path)
    if text is None:
        raise ResearchError(f"{path}: no such file")
    doc, norm = _doc_for(p, None, text, full=True)
    key = doc["plans"][0].get("key")
    st = PS.load_state(p, key) if isinstance(key, str) and PS.KEY_RE.match(key) else None
    if st is not None:
        cur = path_of(p, st)
        if st.get("file") and os.path.abspath(cur) != os.path.abspath(path):
            raise ResearchError(f"plan {key} is already tracked in {_rel(p, cur)}", hint=f"Edit that file, or move it and run `research plan sync {key} --file NEW_PATH`.")
        st["file"] = {**(st.get("file") or {}), "path": _rel(p, path), "sha": (st.get("file") or {}).get("sha"), "revision": (st.get("file") or {}).get("revision", st["revision"])}
        PS.save_state(p, st)
        return _sync_one(p, key, auto=False)
    r = PS.apply(p, norm, source="file")
    st = PS.load_state(p, key)
    _record(p, st, path, text)
    return {"plan": key, "action": "created", "operations": r["operations"], "file": info(p, key), "revision": st["revision"], "plan_id": st["plan_id"]}


# ----------------------------------------------------------------------------------------------- editing without an editor
def _mutate(p: Project, key: str, fn) -> Dict[str, Any]:
    """Apply `fn(plan_dict)` to the plan. Pending edits in the file are synced first, so nothing a person typed is lost."""
    st = PS._state_for(p, key)
    key = st["plan_key"]
    if st.get("file") and info(p, key)["status"] in ("edited", "merge"):
        _sync_one(p, key, auto=False)
    doc = yamlio.load(PS.export(p, key, scope="full").split("\n", 1)[1])
    pl = doc["plans"][0]
    pl.pop("revision", None)
    fn(pl)
    r = PS.apply(p, yamlio.dump(doc, block=True), source="set")
    return {"plan": key, "operations": r["operations"], "file": write(p, key), "revision": PS.load_state(p, key)["revision"]}


def _tasks(pl: Dict[str, Any]):
    for t in pl.get("tasks") or []:
        yield t
    for e in pl.get("experiments") or []:
        for t in e.get("tasks") or []:
            yield t


def _target(pl: Dict[str, Any], name: str) -> Tuple[str, Dict[str, Any]]:
    if name in ("plan", "execution"):
        return name, pl
    up = name.upper()
    for t in _tasks(pl):
        if t.get("key") == name or t.get("id") == up:
            return "task", t
    for e in pl.get("experiments") or []:
        if e.get("key") == name or e.get("id") == up:
            return "experiment", e
    raise ResearchError(f"{name!r} is not a task or experiment of this plan (use a key or an id like T-023), nor `execution`")


def parse_assignment(s: str) -> Tuple[str, List[str], Any]:
    m = re.match(r"^([A-Za-z0-9_.-]+?)\.([A-Za-z0-9_.]+)=(.*)$", s, re.S)
    if not m:
        raise ResearchError(f"cannot read {s!r}", hint="Write TARGET.FIELD=VALUE, for example T-023.max_attempts=6, execution.max_total_attempts=60 or analyze.profile=claude-deep.")
    target, field, raw = m.group(1), m.group(2), m.group(3)
    try:
        val = yamlio.load(raw) if raw.strip() else None
    except Exception:    # noqa: BLE001 - free text that is not YAML is still a value
        val = raw
    return target, field.split("."), val


def set_values(p: Project, key: str, assignments: List[str]) -> Dict[str, Any]:
    """`T-023.max_attempts=6`, `execution.max_total_attempts=60`, `analyze.profile=claude-deep`, `fit.prompt="..."`: validated like any plan edit."""
    parsed = [parse_assignment(a) for a in assignments]

    def fn(pl: Dict[str, Any]) -> None:
        for target, path, val in parsed:
            kind, node = _target(pl, target)
            if kind == "execution":
                path = ["execution"] + path
            elif kind == "task" and len(path) == 1 and path[0] in SHORT:
                path = SHORT[path[0]].split(".")
            for seg in path[:-1]:
                nxt = node.get(seg)
                if not isinstance(nxt, dict):
                    nxt = node[seg] = {}
                node = nxt
            if val is None:
                node.pop(path[-1], None)
            else:
                node[path[-1]] = val
    return _mutate(p, key, fn)


def bump(p: Project, tid: str, attempts: Optional[int] = None, add: int = 3) -> Dict[str, Any]:
    """A task ran out of attempts: allow more (and widen the plan's own budget when that is what stops it), then retry it now.
    `attempts` sets the task's allowance; otherwise `add` more than it has used. Attempt history is untouched."""
    from . import supervisor as SV
    from .schema import normalize_id
    tid = normalize_id(tid, "T")
    hit = PS.find_task_key(p, tid)
    if not hit:
        raise ResearchError(f"{tid} is not part of a plan")
    st, tkey = hit
    SV.require_human(p, "Raising an attempt budget")
    rs = SV.load_run(p, st["plan_id"]) or {}
    e = (rs.get("tasks") or {}).get(tid) or {}
    used = len([a for a in e.get("attempts") or [] if not a.get("voided")])
    c = SV.cfg(p, st)
    spec = (st.get("spec_tasks") or {}).get(tkey) or {}
    before = int((spec.get("execution") or {}).get("max_attempts") or c["max_attempts_per_task"])
    new = int(attempts) if attempts else max(before, used) + int(add)
    if new <= used:
        raise ResearchError(f"{tid} has already used {used} attempt(s); {new} would not allow another", hint=f"Use a number above {used}, or omit it to add {add}.")
    spent, total = int((rs.get("budgets") or {}).get("attempts") or 0), int(c["max_total_attempts"])
    need_total = spent + (new - used)
    out = {"task": tid, "key": tkey, "plan": st["plan_key"], "used": used, "before": before, "after": new, "plan_budget": {"spent": spent, "before": total, "after": max(total, need_total)}}

    def fn(pl: Dict[str, Any]) -> None:
        _, t = _target(pl, tkey)
        t.setdefault("execution", {})["max_attempts"] = new
        if need_total > total:
            pl.setdefault("execution", {})["max_total_attempts"] = need_total
    r = _mutate(p, st["plan_key"], fn)
    out.update(file=r["file"], revision=r["revision"])
    if rs:
        out["released"] = SV.release_budget_blocks(p, st["plan_key"], only=tid)
    return out


# ----------------------------------------------------------------------------------------------- the supervisor's pass
def step(p: Project, out: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Called once per supervisor pass, before scheduling: apply what changed in the plan files. Never raises."""
    res: List[Dict[str, Any]] = []
    try:
        states = [s for s in PS.all_states(p) if s.get("file") and not s.get("archived")]
    except (OSError, ValueError):
        return res
    for s in states:
        key = s["plan_key"]
        try:
            i = info(p, key)
            if i["status"] == "in_sync" and not (i.get("problem") or i.get("held")):
                continue
            if i["status"] == "missing":       # a deleted file is not an instruction to delete the plan
                continue
            r = _sync_one(p, key, auto=True)
        except Exception as ex:    # noqa: BLE001 - one bad plan file must not stop the pass
            r = {"plan": key, "action": "error", "errors": [f"{type(ex).__name__}: {ex}"]}
        res.append(r)
        if out is not None and r["action"] in ("applied", "merged"):
            out.setdefault("events", []).append(f"{key}: plan file applied ({r['operations']} change(s), revision {r.get('revision')})")
        _notify(p, key, r)
    return res


def _notify(p: Project, key: str, r: Dict[str, Any]) -> None:
    try:
        from . import notify
        if not notify.chat_enabled(p):
            return
        from .chat import alerts
        path = (r.get("file") or {}).get("path") or key
        if r["action"] == "invalid" and r.get("fresh"):
            alerts.raise_(p, f"planfile:{key}", "approval_needed", "warn",
                          f"{path} was edited but cannot be applied, so plan {key} keeps running on its last good version:\n- " + "\n- ".join(e[:300] for e in r["errors"][:5]))
        elif r["action"] == "held" and r.get("fresh"):
            alerts.raise_(p, f"planfile:{key}", "approval_needed", "warn",
                          f"{path} has changes that need you before they apply ({'; '.join(r.get('scientific') or [])[:400]}). Run `research plan sync {key}` on your computer.")
        elif r["action"] in ("applied", "merged", "none"):
            alerts.clear(p, f"planfile:{key}", f"{path} is applied (plan {key}, revision {r.get('revision') or (r.get('file') or {}).get('revision')}).")
    except Exception:    # noqa: BLE001 - a notification must never undo a sync
        pass
