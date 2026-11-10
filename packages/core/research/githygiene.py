"""Git hygiene: what is uncommitted, what matters, and a reviewed way to commit it. Three places are kept apart and never mixed:

* the MAIN repository      human-owned. Research OS never commits an arbitrary file here: only files a person named (or the scaffolding it generated),
                           after seeing the staged diff and confirming. Never secrets, caches or runtime state. Never pushes, rewrites history or deletes.
* EXPERIMENT worktrees     the supervisor commits verified work on the experiment's own branch (`commit_verified`), with the same safe file selection.
* the LEDGER               Research OS's own records (`.research/`, shared ledger). Reported read-only; its own checkpoint commands own it.

`report` is read-only. `preflight` runs before a plan starts/resumes and before promotion is authorized: it reports what matters and why, and only blocks
when `git.preflight` is `block`. Every git call is non-interactive (no prompts, editors, pagers or credential helpers), so a headless agent gets an
actionable error instead of a hang.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .util import PolicyBlocked, ResearchError

NONINTERACTIVE = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": ":", "GIT_SEQUENCE_EDITOR": ":", "GIT_MERGE_AUTOEDIT": "no", "GIT_PAGER": "cat", "PAGER": "cat", "GIT_ASKPASS": "true"}

# What Research OS may ignore on its own, because it is regenerable or internal. Never source, plans, reports or documentation.
GENERATED_IGNORES = [".research/", ".research.pre-ledger/", ".venv/", "venv/", "__pycache__/", "*.py[cod]", "*.egg-info/", ".pytest_cache/", ".mypy_cache/",
                     ".ruff_cache/", ".ipynb_checkpoints/", ".DS_Store", "node_modules/", ".claude/skills/", ".agents/skills/"]
BLOCK_BEGIN, BLOCK_END = "# >>> Research OS: generated and internal files (safe to edit outside this block) >>>", "# <<< Research OS <<<"

_SECRET = ("*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*", ".netrc", "credentials.json", "*credentials*.json", "*secret*", "*.token", "token.json",
           ".env", ".env.*", "*.env")
_SECRET_OK = (".env.example", ".env.sample", ".env.template", "*.pub")
_GENERATED = ("__pycache__", "*.pyc", "*.pyo", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints", ".DS_Store", "*.egg-info", ".cache",
              "*.so", "*.o", "*.pyd", "*.class", "build", "dist", "*.swp", ".tox", ".coverage", "htmlcov")
_RUNTIME = (".research", ".research.pre-ledger", ".claude/skills", ".agents/skills", ".research.lock")
_SCAFFOLD = (".gitignore", "README.md", "AGENTS.md", "CLAUDE.md", "RESEARCH.md", ".mcp.json", "opencode.json", ".claude/settings.json", ".claude/settings.local.json",
             ".claude/agents", ".claude/commands", ".codex", ".opencode", ".agents/AGENTS.md", ".github/workflows/pages.yml", ".github/workflows/research-site.yml")
_CODE = (".py", ".r", ".jl", ".sh", ".bash", ".js", ".ts", ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go", ".sql", ".m", ".cu", ".java", ".ipynb", ".slurm", ".sbatch", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml")
_DOC = (".md", ".qmd", ".rst", ".txt", ".tex", ".bib", ".rmd")
_FIGURE = (".png", ".svg", ".pdf", ".jpg", ".jpeg", ".eps")
_BULK = (".h5", ".hdf5", ".npy", ".npz", ".pt", ".pth", ".ckpt", ".safetensors", ".parquet", ".nc", ".zarr", ".tar", ".gz", ".zip", ".bin", ".pkl", ".pickle", ".feather")
_BULK_DIRS = ("outputs", "results", "data", "checkpoints", "runs", "logs")
SCAFFOLD_KINDS = ("scaffold", "website")
IMPORTANT = ("plan", "source", "doc", "report", "figure", "scaffold", "website")      # never silently ignored
MEANINGFUL = IMPORTANT + ("other",)


# ----------------------------------------------------------------------------------------------- git, non-interactively
def git(cwd: str, *args: str, check: bool = True, timeout: float = 60.0, stdin: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> str:
    """Run git without ever waiting for a person. A failure is a ResearchError that says what to do."""
    e = {**os.environ, **NONINTERACTIVE, **(env or {})}
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=e, input=stdin)
    except subprocess.TimeoutExpired:
        raise ResearchError(f"git {args[0]} did not finish in {timeout:g}s (a hook, a signing prompt or a credential helper may be waiting for input)",
                            hint="Nothing was changed. Run the same git command in a terminal to see what it waits for.")
    except OSError as ex:
        raise ResearchError(f"git is not available: {ex}")
    if check and r.returncode:
        msg = (r.stderr or r.stdout).strip()[-400:]
        hint = ("Set your identity: git config user.name 'Your Name'; git config user.email you@example.com" if any(x in msg for x in ("tell me who you are", "empty ident", "auto-detection is disabled", "no email was given"))
                else "If a commit hook rejected it, fix what the hook reports and retry; nothing was committed." if args[0] == "commit" else None)
        raise ResearchError(f"git {args[0]} failed: {msg}", hint=hint)
    return r.stdout if r.returncode == 0 else ""


def top(path: str) -> Optional[str]:
    out = git(path, "rev-parse", "--show-toplevel", check=False).strip()
    return os.path.realpath(out) if out else None


# ----------------------------------------------------------------------------------------------- classification
def _match(name: str, pats: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, x) for x in pats)


def _parts(path: str) -> List[str]:
    return [x for x in path.replace("\\", "/").strip("/").split("/") if x]


def _is_plan(root: str, path: str) -> bool:
    low = path.lower()
    if not low.endswith((".yaml", ".yml")):
        return False
    if "plan" in os.path.basename(low) or low.startswith(("plans/", "plan/")):
        return True
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
        return bool(re.search(r"^plans:\s*$", head, re.M) and "schema_version" in head)
    except OSError:
        return False


def classify(root: str, path: str, guide_dir: Optional[str] = None) -> Dict[str, Any]:
    """{kind, matters, why}. `matters` = a person should look at it; false = noise that belongs in .gitignore."""
    path = path.rstrip("/")
    parts, name = _parts(path), os.path.basename(path)
    lower = [x.lower() for x in parts]
    if _match(name, _SECRET_OK):
        pass
    elif _match(name, _SECRET) or any(_match(x, (".ssh",)) for x in parts):
        return {"kind": "secret", "matters": True, "why": "looks like a secret or credential: Research OS never commits it. Add it to .gitignore and keep it out of the project."}
    if any(path == r or path.startswith(r + "/") for r in _RUNTIME) or parts[:1] == [".research"]:
        return {"kind": "runtime", "matters": False, "why": "Research OS's internal runtime state; it belongs in .gitignore."}
    if any(_match(x, _GENERATED) for x in parts):
        return {"kind": "generated", "matters": False, "why": "a regenerable cache or compiled file; it belongs in .gitignore."}
    if guide_dir and (path == guide_dir or path.startswith(guide_dir.rstrip("/") + "/")):
        if "_output" in parts or "_site" in parts or ".quarto" in parts or "site_libs" in parts:
            return {"kind": "generated", "matters": False, "why": "a built website folder; it is rebuilt by `research site build`."}
        return {"kind": "website", "matters": True, "why": "part of the research website (Quarto sources): human-reviewed content."}
    if any(path == s or path.startswith(s.rstrip("/") + "/") for s in _SCAFFOLD):
        return {"kind": "scaffold", "matters": True, "why": "project scaffolding Research OS generated (rules and agent configuration): commit it so every worktree and clone has the same rules."}
    if _is_plan(root, path):
        return {"kind": "plan", "matters": True, "why": "a research plan: experiment worktrees start from the last commit, so an uncommitted plan file is not in them and is not reproducible."}
    ext = os.path.splitext(name)[1].lower()
    if ext in _BULK or (any(x in _BULK_DIRS for x in lower[:-1]) and ext not in _FIGURE + _DOC):
        return {"kind": "output", "matters": False, "why": "bulky or disposable output (data, checkpoints, logs): usually ignored or kept on the cluster, not in git."}
    if ext in _FIGURE:
        return {"kind": "figure", "matters": True, "why": "a figure: committed when it backs a report or finding, ignored when it is a throwaway."} if not any(x in _BULK_DIRS for x in lower[:-1]) else \
            {"kind": "output", "matters": False, "why": "a figure under an outputs folder; the supervisor keeps small ones with verified work on the experiment branch."}
    if ext in _DOC or "docs" in lower[:-1] or "reports" in lower[:-1]:
        return {"kind": "report" if "reports" in lower[:-1] else "doc", "matters": True, "why": "a document or report: human-written content that is not in git yet, so it is not versioned or reproducible."}
    if ext in _CODE or name in ("Makefile", "Dockerfile", "environment.yml", "requirements.txt"):
        return {"kind": "source", "matters": True, "why": "source code: experiment worktrees only contain committed code, so agents and runs will not see this file."}
    return {"kind": "other", "matters": True, "why": "an unclassified file: look at it and either commit it or ignore it."}


# ----------------------------------------------------------------------------------------------- the main repository
def _guide_dir(p) -> Optional[str]:
    try:
        from . import provenance as PV
        return os.path.relpath(PV.guide_dir(p), PV.main_root(p)).replace(os.sep, "/")
    except Exception:       # noqa: BLE001
        return None


def entries(root: str, guide_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """`git status` as classified entries: {path, xy, state: modified|staged|untracked|ignored, kind, matters, why}. Untracked folders stay collapsed."""
    out = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignored=traditional")
    rows: List[Dict[str, Any]] = []
    items = out.split("\0")
    i = 0
    while i < len(items):
        it = items[i]
        i += 1
        if len(it) < 4:
            continue
        xy, path = it[:2], it[3:]
        if xy[0] in "RC" and i < len(items):
            i += 1                                   # the source path of a rename
        state = "ignored" if xy == "!!" else "untracked" if xy == "??" else "staged" if xy[0] != " " and xy[1] == " " else "modified"
        c = classify(root, path, guide_dir)
        if state == "untracked" and path.endswith("/") and c["kind"] not in ("generated", "runtime", "output", "secret"):
            # a folder that may hold plans, code or reports: classify each file in it (ignored files are not listed)
            for f in git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", path, check=False).split("\0")[:500]:
                if f:
                    rows.append({"path": f, "xy": xy, "state": state, **classify(root, f, guide_dir)})
            continue
        rows.append({"path": path, "xy": xy, "state": state, **c})
    return rows


def main_report(p) -> Dict[str, Any]:
    root = top(p.root) or p.root
    gd = _guide_dir(p)
    ent = entries(root, gd)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or None
    head = git(root, "rev-parse", "--short", "HEAD", check=False).strip() or None
    tracked = [e for e in ent if e["state"] in ("modified", "staged")]
    untracked = [e for e in ent if e["state"] == "untracked"]
    ignored = [e for e in ent if e["state"] == "ignored"]
    return {"path": root, "branch": branch, "head": head, "tracked_changes": tracked, "untracked": untracked,
            "untracked_meaningful": [e for e in untracked if e["matters"] and e["kind"] != "secret"], "untracked_noise": [e for e in untracked if not e["matters"]],
            "secrets": [e for e in ent if e["kind"] == "secret" and e["state"] != "ignored"], "ignored": ignored,
            "ignored_important": [e for e in ignored if e["kind"] in IMPORTANT]}


# ----------------------------------------------------------------------------------------------- worktrees, promotions, ledger
def worktrees(p) -> List[Dict[str, Any]]:
    from . import promotion as PR, workspace as W
    main = PR._main_branch(p)
    out = []
    for w in W.list_workspaces(p):
        try:
            s = W.show(p, w["id"])
        except ResearchError:
            continue
        row = {"id": w["id"], "experiment": w.get("experiment"), "branch": s.get("branch"), "path": s.get("path"), "present": bool(s.get("present")),
               "dirty": 0, "untracked": 0, "unpromoted_commits": 0, "files": []}
        if row["present"] and s.get("path") and os.path.isdir(s["path"]):
            ent = [e for e in entries(s["path"]) if e["state"] != "ignored"]
            row["dirty"] = len([e for e in ent if e["state"] in ("modified", "staged")])
            row["untracked"] = len([e for e in ent if e["state"] == "untracked"])
            row["files"] = [e["path"] for e in ent][:30]
            n = git(s["path"], "rev-list", "--count", f"{main}..HEAD", check=False).strip()
            row["unpromoted_commits"] = int(n) if n.isdigit() else 0
        out.append(row)
    return out


def promotions(p) -> List[Dict[str, Any]]:
    from . import promotion as PR
    try:
        return [{"id": c["id"], "state": c.get("state"), "experiment": c.get("experiment"), "branch": c.get("branch"), "title": c.get("title")}
                for c in PR.list_candidates(p) if c.get("state") not in ("promoted", "abandoned", "rejected")]
    except (ResearchError, OSError, KeyError):
        return []


def ledger_report(p) -> Dict[str, Any]:
    """Read-only: the ledger is Research OS's own and is checkpointed by its own commands, never by the commit action here."""
    from . import workspace as W
    loc = W.locate(p.root)
    path = loc.ledger_root if loc else p.root
    dirty = None
    if loc and loc.linked and os.path.isdir(path) and top(path):
        dirty = len([l for l in git(path, "status", "--porcelain", check=False).splitlines() if l.strip()])
    return {"path": path, "shared": bool(loc and loc.linked), "uncommitted_files": dirty}


def report(p) -> Dict[str, Any]:
    """Everything, separately: main, worktrees, promotions, ledger, and the files that need a person."""
    try:
        m = main_report(p)
    except ResearchError as ex:
        return {"ok": False, "error": str(ex), "main": None, "worktrees": [], "promotions": [], "ledger": {}, "attention": []}
    wts, prs = worktrees(p), promotions(p)
    attention = [{"path": e["path"], "kind": e["kind"], "state": e["state"], "why": e["why"]} for e in m["tracked_changes"] + m["untracked_meaningful"]]
    return {"ok": True, "main": m, "worktrees": wts, "promotions": prs, "ledger": ledger_report(p), "attention": attention,
            "summary": {"tracked_changes": len(m["tracked_changes"]), "untracked": len(m["untracked_meaningful"]), "noise": len(m["untracked_noise"]),
                        "secrets": len(m["secrets"]), "dirty_worktrees": len([w for w in wts if w["dirty"] or w["untracked"]]),
                        "unpromoted_commits": sum(w["unpromoted_commits"] for w in wts), "pending_promotions": len(prs)}}


# ----------------------------------------------------------------------------------------------- preflight
def _issue(sev: str, what: str, why: str, do: str, files: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"severity": sev, "what": what, "why": why, "do": do, "files": (files or [])[:12]}


def preflight(p, purpose: str = "plan") -> Dict[str, Any]:
    """Before a plan starts/resumes (`purpose="plan"`) or code promotion is authorized (`"promotion"`). Never prompts, never changes anything.
    `ok` is false only when `git.preflight` is `block` and something that matters is open; the default `warn` reports and continues."""
    from . import registry
    mode = registry.section(p, "git").get("preflight", "warn")
    if mode == "off":
        return {"purpose": purpose, "mode": mode, "ok": True, "issues": [], "summary": {}}
    r = report(p)
    if not r.get("ok"):
        return {"purpose": purpose, "mode": mode, "ok": True, "issues": [_issue("notice", "git status could not be read", r.get("error", ""), "Run `git status` to see why.")], "summary": {}}
    m, issues = r["main"], []
    tc = [e["path"] for e in m["tracked_changes"] if e["matters"]]          # a modified tracked cache or runtime file is noise, not work to review
    if tc:
        if purpose == "promotion":
            issues.append(_issue("blocking", f"{len(tc)} tracked file(s) in {m['branch']} have uncommitted changes",
                                 "promotion fast-forwards main and is refused while main has uncommitted changes", "Review and commit them (`research git commit --paths notes.md`) or stash them.", tc))
        else:
            issues.append(_issue("warning", f"{len(tc)} tracked file(s) in {m['branch']} have uncommitted changes",
                                 "experiment worktrees start from the last commit, so agents and runs will NOT see these edits and results will not match what you see here",
                                 "Review and commit them (`research git commit --paths notes.md`), or accept that the plan runs on the last commit.", tc))
    by_kind: Dict[str, List[str]] = {}
    for e in m["untracked_meaningful"]:
        by_kind.setdefault(e["kind"], []).append(e["path"])
    for kind, files in sorted(by_kind.items()):
        why = classify(p.root, files[0], _guide_dir(p))["why"]
        sev = "warning" if kind in ("plan", "source", "scaffold", "website") else "notice"
        issues.append(_issue(sev, f"{len(files)} untracked {kind} file(s)", why, "Review, then commit what you want versioned: `research git commit --paths notes.md --yes` "
                             + ("(or `research git commit --scaffold --yes` for generated scaffolding)" if kind in SCAFFOLD_KINDS else ""), files))
    if m["secrets"]:
        issues.append(_issue("warning", f"{len(m['secrets'])} file(s) look like secrets and are not ignored", m["secrets"][0]["why"], "Add them to .gitignore (`research git ignore --pattern NAME`) before any `git add`.",
                             [e["path"] for e in m["secrets"]]))
    if m["untracked_noise"]:
        issues.append(_issue("notice", f"{len(m['untracked_noise'])} generated or internal path(s) are not ignored", m["untracked_noise"][0]["why"], "Run `research git ignore --apply` (adds a managed block to .gitignore; nothing else changes).",
                             [e["path"] for e in m["untracked_noise"]]))
    if m["ignored_important"]:
        issues.append(_issue("warning", f"{len(m['ignored_important'])} ignored file(s) look important", "an ignore rule hides what looks like source, a plan, a report or documentation, so it is never versioned",
                             "Check the .gitignore rule that matches them; Research OS never adds such a rule.", [e["path"] for e in m["ignored_important"]]))
    wts = r["worktrees"]
    dirty = [w for w in wts if w["dirty"] or w["untracked"]]
    if dirty:
        issues.append(_issue("notice", f"{len(dirty)} experiment worktree(s) have uncommitted work", "only committed work can be promoted; the supervisor commits verified work on the experiment branch",
                             "Let the supervisor verify the task, or commit in that worktree.", [f"{w['id']} ({w['dirty']} modified, {w['untracked']} untracked)" for w in dirty]))
    un = [w for w in wts if w["unpromoted_commits"]]
    if un:
        issues.append(_issue("notice", f"{sum(w['unpromoted_commits'] for w in un)} verified commit(s) in {len(un)} worktree(s) are not promoted", "they are safe on their branches but not in main yet",
                             "`research promote prepare WS` starts a promotion; it needs tests, review and your approval.", [f"{w['id']} +{w['unpromoted_commits']}" for w in un]))
    if r["promotions"]:
        issues.append(_issue("notice", f"{len(r['promotions'])} promotion(s) are pending", "each waits for a gate (tests, review or your approval)", "`research promote list`", [f"{x['id']} {x['state']}" for x in r["promotions"]]))
    blocking = [i for i in issues if i["severity"] == "blocking" or (mode == "block" and i["severity"] == "warning")]
    return {"purpose": purpose, "mode": mode, "ok": not blocking, "issues": issues, "blocking": blocking, "summary": r["summary"]}


def require_preflight(p, purpose: str) -> Dict[str, Any]:
    """The check plan_run / plan_resume / promotion call: a result to attach, or a refusal when `git.preflight` is `block` (or promotion has a tracked-dirty main)."""
    try:
        r = preflight(p, purpose)
    except Exception as ex:           # noqa: BLE001 - never let the check itself stop work
        return {"purpose": purpose, "ok": True, "issues": [_issue("notice", "git preflight could not run", str(ex), "")], "summary": {}}
    hard = [i for i in r.get("blocking") or [] if i["severity"] == "blocking" or r.get("mode") == "block"]
    if hard and r.get("mode") == "block":
        raise PolicyBlocked("Git preflight (git.preflight = block): " + "; ".join(i["what"] for i in hard[:4]) + ". " + hard[0]["do"],
                            hint="`research git status` lists every file and why it matters. Set git.preflight to warn to continue anyway.")
    return r


def lines(r: Dict[str, Any]) -> List[str]:
    """Plain-text rendering for CLI and chat."""
    if not r or not r.get("issues"):
        return []
    mark = {"blocking": "✗", "warning": "!", "notice": "-"}
    out = [f"Git preflight ({r.get('purpose')}): {len(r['issues'])} thing(s) to know"]
    for i in r["issues"]:
        out.append(f"  {mark.get(i['severity'], '-')} {i['what']}")
        out.append(f"      why: {i['why']}")
        if i.get("files"):
            out.append("      " + ", ".join(i["files"][:5]) + (" …" if len(i["files"]) > 5 else ""))
        if i.get("do"):
            out.append(f"      do:  {i['do']}")
    return out


# ----------------------------------------------------------------------------------------------- generated-file ignores
def _block_text(patterns: List[str]) -> str:
    return BLOCK_BEGIN + "\n" + "\n".join(patterns) + "\n" + BLOCK_END + "\n"


def _hides(root: str, pattern: str) -> List[str]:
    """Important files (tracked, or untracked and not yet ignored) that this ignore pattern would hide."""
    with tempfile.NamedTemporaryFile("w", suffix=".ignore", delete=False) as f:
        f.write(pattern + "\n")
        name = f.name
    try:
        hit = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--ignored", f"--exclude-from={name}", check=False)
        hit += git(root, "ls-files", "-z", "--cached", "--ignored", f"--exclude-from={name}", check=False)
    finally:
        os.unlink(name)
    return sorted({x for x in hit.split("\0") if x and classify(root, x)["kind"] in IMPORTANT})


def ignore_plan(p, extra: Optional[List[str]] = None) -> Dict[str, Any]:
    """Which generated/internal patterns the managed .gitignore block would add; a pattern that would hide an important file is refused and named."""
    root = top(p.root) or p.root
    gi = os.path.join(root, ".gitignore")
    text = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    present = {l.strip() for l in text.splitlines()}
    wanted, refused = [], []
    for pat in GENERATED_IGNORES + [x for x in (extra or []) if x]:
        hidden = _hides(root, pat)
        if hidden:
            refused.append({"pattern": pat, "would_hide": hidden[:5], "why": "it would hide a file that looks important; Research OS never ignores those"})
        elif pat not in wanted:
            wanted.append(pat)
    block = re.search(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?", text, re.S)
    existing_in_block = set(block.group(0).splitlines()[1:-1]) if block else set()
    outside = present - existing_in_block
    add = [x for x in wanted if x not in outside]
    new = _block_text(add) if add else ""
    tracked = [x for x in git(root, "ls-files", "-z", check=False).split("\0") if x and classify(root, x)["kind"] in ("generated", "runtime")]
    return {"gitignore": ".gitignore", "add": add, "already_ignored": [x for x in wanted if x in outside], "refused": refused, "block": new, "changes": bool(add) and (not block or block.group(0) != new),
            "tracked_but_generated": tracked[:20], "note": ("Tracked generated files stay tracked: ignoring does not untrack them. `git rm --cached` is yours to run; Research OS never deletes or untracks."
                                                            if tracked else None)}


def ignore_apply(p, extra: Optional[List[str]] = None) -> Dict[str, Any]:
    """Write the managed block. Lines outside it are never touched; re-running updates only the block."""
    r = ignore_plan(p, extra)
    if not r["changes"]:
        return {**r, "written": False}
    root = top(p.root) or p.root
    gi = os.path.join(root, ".gitignore")
    text = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
    pat = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.S)
    new = pat.sub(lambda _: r["block"], text) if pat.search(text) else (text + ("" if not text or text.endswith("\n") else "\n") + ("\n" if text else "") + r["block"])
    from .durability import replace_bytes
    replace_bytes(gi, new.encode("utf-8"), 0o644)
    return {**r, "written": True}


# ----------------------------------------------------------------------------------------------- the reviewed commit (main repository only)
def _safe_rel(root: str, path: str) -> str:
    full = os.path.realpath(os.path.join(root, path))
    if not (full == root or full.startswith(root + os.sep)):
        raise ResearchError(f"{path} is outside the project repository")
    return os.path.relpath(full, root).replace(os.sep, "/")


def _targets(p, root: str, paths: Optional[List[str]], scaffold: bool) -> Tuple[List[str], List[Dict[str, str]]]:
    gd = _guide_dir(p)
    ent = {e["path"].rstrip("/"): e for e in entries(root, gd) if e["state"] != "ignored"}
    chosen: List[str] = []
    refused: List[Dict[str, str]] = []
    if scaffold:
        chosen = [k for k, e in ent.items() if e["kind"] in SCAFFOLD_KINDS and e["state"] in ("untracked", "modified")]
    for raw in paths or []:
        rel = _safe_rel(root, raw).rstrip("/")
        hit = [k for k in ent if k == rel or k.startswith(rel + "/") or rel.startswith(k + "/")]
        if not hit:
            refused.append({"path": rel, "why": "nothing to commit there (unchanged, ignored, or not in this repository)"})
        chosen += hit or []
    final: List[str] = []
    for k in dict.fromkeys(chosen):
        c = ent[k]
        if c["kind"] in ("secret", "runtime", "generated"):
            refused.append({"path": k, "why": c["why"]})
        else:
            final.append(k)
    wt = [w["path"] for w in worktrees(p) if w.get("path")]
    inside = [k for k in final if any(os.path.realpath(os.path.join(root, k)).startswith(os.path.realpath(x) + os.sep) for x in wt)]
    for k in inside:
        refused.append({"path": k, "why": "inside an experiment worktree: that work is committed on its own branch by the supervisor"})
    return [k for k in final if k not in inside], refused


def preview_commit(p, paths: Optional[List[str]] = None, scaffold: bool = False, message: Optional[str] = None) -> Dict[str, Any]:
    """The staged diff of exactly the files that would be committed, built in a throwaway index so the real index is untouched."""
    root = top(p.root) or p.root
    files, refused = _targets(p, root, paths, scaffold)
    msg = message or ("Add Research OS project scaffolding" if scaffold and not paths else "Add reviewed project files")
    out = {"root": root, "files": files, "refused": refused, "message": msg, "stat": "", "diff": "", "committed": False}
    if not files:
        return out
    with tempfile.TemporaryDirectory() as td:
        idx = os.path.join(td, "index")
        env = {"GIT_INDEX_FILE": idx}
        git(root, "read-tree", "HEAD", check=False, env=env)
        git(root, "add", "--", *files, env=env)
        out["stat"] = git(root, "diff", "--cached", "--stat", env=env, check=False)
        d = git(root, "diff", "--cached", env=env, check=False)
        out["diff"] = d if len(d) <= 200_000 else d[:200_000] + "\n# … truncated\n"
        out["binary"] = [l for l in git(root, "diff", "--cached", "--numstat", env=env, check=False).splitlines() if l.startswith("-\t-")]
    return out


def commit(p, paths: Optional[List[str]] = None, scaffold: bool = False, message: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
    """Reviewed, scoped commit of files a person named (or the generated scaffolding), in the main repository only. Without `confirm` it returns the preview and
    commits nothing. Only the listed files are committed (`git commit --only`): anything else you staged stays staged, other modified files stay modified.
    Never pushes. Humans only."""
    from . import promotion as PR
    if not PR._is_human(p):
        raise PolicyBlocked("Only a person commits files on main: agents cannot, and Research OS never commits arbitrary files there by itself",
                            hint="Review the files, then run `research git commit --paths notes.md --yes` yourself.")
    if not paths and not scaffold:
        raise ResearchError("Name the files to commit (--paths notes.md) or use --scaffold for the generated project scaffolding")
    pv = preview_commit(p, paths, scaffold, message)
    if not pv["files"]:
        return {**pv, "needs": "nothing to commit"}
    if not confirm:
        return {**pv, "needs": "confirm: review the diff, then repeat with --yes"}
    root = pv["root"]
    git(root, "add", "--", *pv["files"])
    git(root, "commit", "--only", "-q", "-m", pv["message"] + "\n\nCommitted after review via Research OS (scoped to the listed files).", "--", *pv["files"])
    sha = git(root, "rev-parse", "HEAD").strip()
    p.event("project", None, "git_commit", f"reviewed commit {sha[:8]}: {len(pv['files'])} file(s): {', '.join(pv['files'][:6])}")
    return {**pv, "committed": True, "commit": sha}


def scaffold_hint(p) -> List[str]:
    """Lines to print after Research OS generated files: uncommitted scaffolding and how to commit it (never done automatically)."""
    try:
        m = main_report(p)
    except ResearchError:
        return []
    files = [e["path"] for e in m["untracked"] + m["tracked_changes"] if e["kind"] in SCAFFOLD_KINDS]
    if not files:
        return []
    return [f"{len(files)} generated scaffolding file(s) are not committed yet: {', '.join(files[:5])}{' …' if len(files) > 5 else ''}",
            "  review the staged diff: research git commit --scaffold      commit it: research git commit --scaffold --yes"]


# ----------------------------------------------------------------------------------------------- verified work on an experiment branch
def select_for_commit(path: str, exclude: List[str], keep: List[str], max_bytes: int) -> Dict[str, Any]:
    """Which changed files of an experiment worktree the supervisor commits. Source, tests, reports and small figures go in; secrets, caches, compiled files and
    bulky outputs do not (`exclude` folders/patterns, with `keep` globs for small figures/reports inside them). Returns {add, skipped: [{path, why}]}."""
    ent = [e for e in entries(path) if e["state"] != "ignored"]
    add, skipped = [], []
    for e in ent:
        rel = e["path"].rstrip("/")
        files = ([os.path.relpath(os.path.join(dp, f), path).replace(os.sep, "/") for dp, _, fs in os.walk(os.path.join(path, rel)) for f in fs]
                 if os.path.isdir(os.path.join(path, rel)) else [rel])
        for f in files:
            c = classify(path, f)
            name, parts = os.path.basename(f), _parts(f)
            if c["kind"] in ("secret", "runtime", "generated"):
                skipped.append({"path": f, "why": c["kind"]})
                continue
            excluded = any((x.endswith("/") and x.rstrip("/") in parts[:-1]) or (not x.endswith("/") and fnmatch.fnmatch(name, x)) for x in exclude)
            full = os.path.join(path, f)
            size = os.path.getsize(full) if os.path.isfile(full) else 0
            if excluded and not _match(name, keep):
                skipped.append({"path": f, "why": "excluded (commit_exclude)"})
            elif size > max_bytes:
                skipped.append({"path": f, "why": f"larger than {max_bytes // (1024 * 1024)} MB"})
            else:
                add.append(f)
    # deletions of tracked files show as ' D' entries whose file no longer exists: they are not walked above
    for e in ent:
        if e["xy"].strip() == "D" and e["path"] not in add:
            add.append(e["path"])
    return {"add": sorted(set(add)), "skipped": skipped}
