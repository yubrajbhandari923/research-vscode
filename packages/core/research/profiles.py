"""Settings profiles: named bundles of settings you can compare, apply to a project, and check for "is this safe to leave running overnight".

A profile is only a list of `key: value` pairs over the ordinary settings (registry options); applying one is the same audited, PIN-gated change as setting them
one by one (`registry.apply`), so nothing here bypasses a policy rule. Built-in profiles ship with Research OS (`default`, `interactive`, `overnight`); your own
live on the MACHINE (`profiles.yaml` next to the machine config) so every project can use them, and are applied PER PROJECT. Applying remembers what it replaced
so `revert` can put it back, and `check` reports whether the project is actually set up for the profile, beyond the settings (is the supervisor running, will you
be told, is automatic promotion fully configured, is anything waiting for you).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from . import configfile, registry, yamlio
from .util import ResearchError, now_iso

# The settings a profile may carry: the ones that decide whether a run can be left alone. (Any registry option is accepted in a custom profile.)
UNATTENDED = {
    "supervisor.usage_limit.on_limit": "wait", "supervisor.usage_limit.max_wait_hours": 10, "supervisor.recovery.enabled": True,
    "supervisor.commit_verified_work": True, "supervisor.investigator.auto": True, "notifications.auto_debug": True, "notifications.level": "medium",
    "auto_promotion.auto_approve_verified_code": True, "auto_promotion.auto_retry_attempts": 3,
    "supervisor_assistant.self_heal": True,       # without it a recoverable failure at 2 a.m. still waits for a person
}
BUILTIN: Dict[str, Dict[str, Any]] = {
    "default": {"description": "Research OS's shipped settings for everything a profile controls. Applying it puts those settings back to their defaults.",
                "settings": {k: registry.default(k) for k in UNATTENDED}, "critical": []},
    "interactive": {"description": "You are at the keyboard: stop and ask on a usage limit, no automatic diagnosis, promotions wait for you.",
                    "settings": {"supervisor.usage_limit.on_limit": "halt", "supervisor.recovery.enabled": True, "supervisor.investigator.auto": False,
                                 "notifications.auto_debug": False, "auto_promotion.auto_approve_verified_code": False}, "critical": []},
    "overnight": {"description": "Leave it running: wait out usage limits and resume, diagnose halts by itself, recover late HPC results, and promote verified code under the standing "
                                 "authorization (tests, independent review and provenance still gate every promotion). You are told about successes and interrupted only for decisions.",
                  "settings": dict(UNATTENDED),
                  "critical": ["supervisor.usage_limit.on_limit", "supervisor.recovery.enabled", "auto_promotion.auto_approve_verified_code"]},
}
APPLIED_FILE = "profile.json"


# ------------------------------------------------------------------------------------------------ storage (machine level)
def _path() -> str:
    return os.path.join(os.path.dirname(configfile.machine_path() or os.path.join(os.path.expanduser("~"), ".config", "research-os", "config.yaml")), "profiles.yaml")


def _custom() -> Dict[str, Dict[str, Any]]:
    try:
        with open(_path(), encoding="utf-8") as f:
            raw = yamlio.load(f.read()) or {}
    except OSError:
        return {}
    except Exception as ex:   # noqa: BLE001
        raise ResearchError(f"{_path()} could not be parsed: {ex}", hint="Fix the YAML or move the file aside; built-in profiles still work.")
    return {k: v for k, v in (raw.get("profiles") or {}).items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def _save(profiles: Dict[str, Dict[str, Any]]) -> None:
    from .store import _atomic_write
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    _atomic_write(_path(), "# Research OS settings profiles (machine level). Edit with `research profile`; apply to a project with `research profile apply NAME`.\n"
                  + yamlio.dump({"profiles": profiles}, block=True))


def names() -> List[str]:
    return list(BUILTIN) + [n for n in sorted(_custom()) if n not in BUILTIN]


def get(name: str) -> Dict[str, Any]:
    if name in BUILTIN:
        return {"name": name, "builtin": True, **{k: v for k, v in BUILTIN[name].items()}}
    c = _custom().get(name)
    if c is None:
        raise ResearchError(f"No settings profile {name!r}", hint="`research profile list`")
    return {"name": name, "builtin": False, "description": c.get("description") or "", "settings": dict(c.get("settings") or {}), "critical": list(c.get("critical") or [])}


def list_profiles() -> List[Dict[str, Any]]:
    return [{"name": n, "builtin": n in BUILTIN, "description": get(n)["description"], "settings": len(get(n)["settings"])} for n in names()]


def _validated(settings: Dict[str, Any]) -> Dict[str, Any]:
    out, errs = {}, []
    for k, v in settings.items():
        try:
            registry.get(k)
        except Exception:   # noqa: BLE001
            errs.append(f"{k}: not a setting")
            continue
        ok, nv, err = registry.validate(k, v)
        if ok:
            out[k] = nv
        else:
            errs.append(f"{k}: {err}")
    if errs:
        raise ResearchError("Invalid profile settings: " + "; ".join(errs))
    return out


def parse_pairs(pairs: Optional[List[str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for x in pairs or []:
        k, sep, v = x.partition("=")
        if not sep or not k.strip():
            raise ResearchError(f"{x!r}: use key=value, for example supervisor.usage_limit.max_wait_hours=14")
        try:
            out[k.strip()] = yamlio.load(v) if v.strip() else None
        except Exception:   # noqa: BLE001
            out[k.strip()] = v
    return out


def create(name: str, description: str = "", base: Optional[str] = None, settings: Optional[Dict[str, Any]] = None, from_project=None) -> Dict[str, Any]:
    """A custom profile (machine level). Start from a built-in or custom one (`base`), or from the values a project has now (`from_project`), then override."""
    if name in BUILTIN:
        raise ResearchError(f"{name!r} is a built-in profile; choose another name")
    if not name.replace("-", "").replace("_", "").isalnum():
        raise ResearchError("Use letters, digits, - and _ in a profile name")
    vals: Dict[str, Any] = {}
    if base:
        vals.update(get(base)["settings"])
    if from_project is not None:
        keys = sorted({k for b in BUILTIN.values() for k in b["settings"]} | set(vals))
        vals.update({k: registry.describe_value(from_project, k) for k in keys})
    vals.update(settings or {})
    if not vals:
        raise ResearchError("A profile needs settings: --from NAME, --from-project, or --set key=value")
    custom = _custom()
    custom[name] = {"description": description or (f"based on {base}" if base else "custom"), "settings": _validated(vals), "critical": list((get(base)["critical"] if base else []))}
    _save(custom)
    return get(name)


def delete(name: str) -> None:
    if name in BUILTIN:
        raise ResearchError("Built-in profiles cannot be deleted")
    custom = _custom()
    if name not in custom:
        raise ResearchError(f"No settings profile {name!r}")
    del custom[name]
    _save(custom)


# ------------------------------------------------------------------------------------------------ compare, apply, revert
def _row(p, k: str, want: Any, other: Any = None, with_project: bool = True) -> Dict[str, Any]:
    cur = registry.describe_value(p, k) if with_project else other
    return {"key": k, "label": registry.get(k).label, "profile": want, "current": cur, "differs": want != cur}


def diff(p, a: str, b: Optional[str] = None, all_: bool = False) -> Dict[str, Any]:
    """`diff overnight`: the profile against this project's settings now. `diff default overnight`: two profiles. Only differences unless `all_`."""
    pa = get(a)
    if b is None:
        rows = [_row(p, k, v) for k, v in pa["settings"].items()]
        left, right = a, "this project now"
    else:
        pb = get(b)
        rows = []
        for k in sorted(set(pa["settings"]) | set(pb["settings"])):
            va = pa["settings"].get(k, registry.default(k))
            vb = pb["settings"].get(k, registry.default(k))
            rows.append({"key": k, "label": registry.get(k).label, "profile": vb, "current": va, "differs": va != vb})
        left, right = a, b
    return {"left": left, "right": right, "rows": rows if all_ else [r for r in rows if r["differs"]], "total": len(rows), "differing": sum(r["differs"] for r in rows)}


def _applied_path(p) -> str:
    return p.path(APPLIED_FILE)


def applied(p) -> Optional[Dict[str, Any]]:
    try:
        with open(_applied_path(p), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status(p) -> Dict[str, Any]:
    """Which profile this project applied, and whether it has drifted since (settings changed one by one afterwards)."""
    a = applied(p)
    if not a:
        return {"applied": None}
    try:
        prof = get(a["name"])
    except ResearchError:
        return {"applied": a["name"], "at": a["at"], "drift": None, "note": "the profile no longer exists on this machine"}
    want = {**prof["settings"], **(a.get("overrides") or {})}
    drift = [{"key": k, "profile": v, "current": registry.describe_value(p, k)} for k, v in want.items() if registry.describe_value(p, k) != v]
    return {"applied": a["name"], "at": a["at"], "overrides": a.get("overrides") or {}, "drift": drift}


def preview(p, name: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    prof = get(name)
    vals = _validated({**prof["settings"], **(overrides or {})})
    rows = [_row(p, k, v) for k, v in vals.items()]
    changes = {r["key"]: r["profile"] for r in rows if r["differs"]}
    pv = registry.preview(p, changes) if changes else {"ok": True, "errors": {}, "policy_keys": [], "risky_keys": [], "needs_confirmation": False, "changed": False, "diff": ""}
    return {"profile": name, "rows": rows, "changes": changes, "unchanged": len(rows) - len(changes), "policy_keys": pv.get("policy_keys", []), "risky_keys": pv.get("risky_keys", []),
            "needs_pin": bool(pv.get("policy_keys")), "errors": pv.get("errors", {}), "diff": pv.get("diff", ""),
            "turns_on": [k for k, v in changes.items() if v is True and registry.get(k).type == "bool"]}


def apply(p, name: str, overrides: Optional[Dict[str, Any]] = None, confirm: bool = False, pin: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
    """Apply a profile to THIS project: the changed settings are written through the ordinary Settings path in one audited change (one PIN for the policy ones).
    What they replaced is remembered for `revert`. A profile never reaches machine config or other projects."""
    pv = preview(p, name, overrides)
    if pv["errors"]:
        raise ResearchError("; ".join(f"{k}: {e}" for k, e in pv["errors"].items()))
    before = {k: registry.describe_value(p, k) for k in pv["changes"]}
    res = {"changed": False}
    if pv["changes"]:
        res = registry.apply(p, pv["changes"], scope="project", reason=reason or f"settings profile {name}", confirm=confirm, pin=pin)
    prev = applied(p)
    rec = {"name": name, "at": now_iso(), "overrides": overrides or {}, "before": {**((prev or {}).get("before") or {}), **{k: v for k, v in before.items() if k not in ((prev or {}).get("before") or {})}},
           "by": (p.author or {}).get("author_name")}
    from .store import _atomic_write
    os.makedirs(os.path.dirname(_applied_path(p)), exist_ok=True)
    _atomic_write(_applied_path(p), json.dumps(rec, indent=1, sort_keys=True, default=str) + "\n")
    p.event("config", "profile", "applied", f"settings profile {name} applied: {len(pv['changes'])} setting(s) changed" + (f" ({reason})" if reason else ""))
    return {"profile": name, "changed": res.get("changed", False), "changes": pv["changes"], "unchanged": pv["unchanged"]}


def revert(p, confirm: bool = False, pin: Optional[str] = None) -> Dict[str, Any]:
    a = applied(p)
    if not a or not a.get("before"):
        raise ResearchError("No profile has been applied to this project, or it changed nothing")
    before = a["before"]
    reset = [k for k, v in before.items() if v == registry.default(k)]
    set_ = {k: v for k, v in before.items() if k not in reset}
    if set_:
        registry.apply(p, set_, scope="project", reason=f"revert settings profile {a['name']}", confirm=confirm, pin=pin)
    if reset:
        registry.reset(p, reset, reason=f"revert settings profile {a['name']}")
    os.unlink(_applied_path(p))
    p.event("config", "profile", "reverted", f"settings profile {a['name']} reverted: {len(before)} setting(s) restored")
    return {"reverted": a["name"], "restored": before}


# ------------------------------------------------------------------------------------------------ is this safe to leave running?
def check(p, name: str = "overnight") -> Dict[str, Any]:
    """Is this project actually set up for the profile? Settings that differ from it, and the things settings cannot say: the supervisor running, a way to reach you,
    automatic promotion fully configured and authorized, nothing waiting for you, enough budget left. `ready` is false when any blocker is open."""
    prof = get(name)
    checks: List[Dict[str, Any]] = []

    def add(id_: str, status: str, what: str, fix: Optional[str] = None) -> None:
        checks.append({"id": id_, "status": status, "what": what, "fix": fix})
    for k, want in prof["settings"].items():
        cur = registry.describe_value(p, k)
        if cur == want:
            add(k, "ok", f"{registry.get(k).label}: {cur!r}")
        else:
            add(k, "fail" if k in prof["critical"] else "warn", f"{registry.get(k).label} is {cur!r}, {name} expects {want!r}", f"research profile apply {name}  (or: research config set {k} {want})")
    if name != "overnight" and not prof["critical"]:
        return _verdict(name, checks)
    from . import supervisor as SV
    d = SV.daemon_status(p)
    if not d.get("running"):
        add("supervisor", "fail", "the supervisor is not running: nothing runs, retries or resumes unattended without it", "research supervisor start")
    elif d.get("restart_required"):
        add("supervisor", "warn", d.get("restart_reason") or "the supervisor runs older code", "research supervisor restart")
    else:
        add("supervisor", "ok", "the supervisor is running")
    from . import notify
    if notify.chat_enabled(p):
        add("chat", "ok", "Discord/Telegram is set up: you will be told about successes and anything that needs you")
    else:
        add("chat", "warn", "no chat is configured: halts, promotions and decisions will only show in the terminal", "research chat setup discord")
    if prof["settings"].get("auto_promotion.auto_approve_verified_code"):
        from . import autopromote as AP
        c = AP.policy(p)
        if not c["test_command"]:
            add("promotion_tests", "fail", "no promotion test command: nothing can be verified or promoted", "research config set promotion.test_command 'pytest -q'")
        elif not c["reviewer_profile"] or c["reviewer_profile"] not in ((p.config.get("agents") or {}).get("profiles") or {}):
            add("promotion_reviewer", "fail", "no independent reviewer profile is configured for automatic promotion", "research config set auto_promotion.reviewer_profile PROFILE")
        else:
            add("promotion_setup", "ok", f"promotion tests ({c['test_command']}) and an independent reviewer ({c['reviewer_profile']}) are configured")
        if AP.standing_enabled(p) and not AP._standing_read(p):
            add("standing_authorization", "fail", "the setting is on but no human bound it (it was edited outside Settings)", "research config set auto_promotion.auto_approve_verified_code true")
        elif AP.standing_enabled(p) and (AP._standing_read(p) or {}).get("fingerprint") != AP.fingerprint(p):
            add("standing_authorization", "fail", "the verification policy changed outside Settings; the authorization is suspended", "research config set auto_promotion.auto_approve_verified_code true")
        elif AP.standing_enabled(p):
            add("standing_authorization", "ok", f"standing authorization is bound to the current verification policy (by {(AP._standing_read(p) or {}).get('by')})")
    try:
        from . import githygiene as GH
        issues = [i for i in (GH.preflight(p, "promotion").get("issues") or []) if i.get("severity") != "notice"]
        add("git", "warn" if issues else "ok", (f"{len(issues)} uncommitted item(s) could block a promotion or be invisible to agents: " + "; ".join(str(i.get('what')) for i in issues[:3])) if issues else "main is clean",
            "research git status" if issues else None)
    except Exception:   # noqa: BLE001
        pass
    from . import decisions as D
    waiting = D.list_all(p, "open")
    add("decisions", "warn" if waiting else "ok", f"{len(waiting)} decision(s) wait for you; a plan cannot pass them unattended" if waiting else "nothing is waiting for you", "research decisions" if waiting else None)
    low = []
    for rs in SV.all_runs(p):
        if rs.get("state") in ("running", "paused", "attention", "blocked"):
            st = SV.cfg(p, __import__("research.planspec", fromlist=["x"]).load_state(p, rs["plan_key"]))
            left = int(st["max_total_attempts"]) - int((rs.get("budgets") or {}).get("attempts") or 0)
            if left < 10:
                low.append(f"{rs['plan_key']} has {left} attempt(s) left")
    add("budget", "warn" if low else "ok", ("; ".join(low) + ": it will block overnight") if low else "attempt budgets have room", "research task budget T-… --max-attempts N · research task intervene T-… plan-budget N" if low else None)
    lim = [x for x in SV.limits_status(p) if x["limited"]]
    if lim:
        add("provider_limit", "warn", "a provider is limited right now: " + ", ".join(f"{x['account']} until {SV._hhmm(x['until'])}" for x in lim) + "; with `wait` the tasks resume after the reset")
    return _verdict(name, checks)


def _verdict(name: str, checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    blockers = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    return {"profile": name, "ready": not blockers, "blockers": len(blockers), "warnings": len(warns), "checks": checks,
            "verdict": (f"READY for {name}" + (f" ({len(warns)} warning(s))" if warns else "")) if not blockers else f"NOT ready for {name}: {len(blockers)} blocker(s)"}
