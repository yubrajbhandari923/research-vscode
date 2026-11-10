"""`research` command-line interface. Thin layer over the `research` core package.

Exit codes: 0 ok · 1 error · 2 not initialized · 3 run budget / policy block · 4 not found.
Every command accepts --json. Parser assembly and dispatch only; the commands live in `commands/`.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

import research
from research.store import Project
from research.util import ResearchError, detect_author

from .commands import MUTATING, agents, chat, decide, git, guide, heal, hpc, job, plans, profile, project, records, site, skills, supervisor
from .output import dim, red

def build_parser() -> argparse.ArgumentParser:
    def _common(suppress: bool) -> argparse.ArgumentParser:
        c = argparse.ArgumentParser(add_help=False)
        kw = {"default": argparse.SUPPRESS} if suppress else {}
        c.add_argument("--json", action="store_true", help="machine-readable output", **kw)
        c.add_argument("--root", help="project root (default: search upwards from cwd, or $RESEARCH_ROOT)", **kw)
        c.add_argument("--agent", help="act as an agent with this name (or set RESEARCH_AGENT)", **kw)
        c.add_argument("--model", help="agent model name (or RESEARCH_AGENT_MODEL)", **kw)
        c.add_argument("--as", dest="as_", choices=["human", "agent", "system"], help="author type override", **kw)
        return c

    common = _common(True)
    ap = argparse.ArgumentParser(prog="research", parents=[_common(False)],
                                 description="Project-local research memory: questions → experiments → runs → findings → decisions → checkpoints.",
                                 epilog="Exit codes: 0 ok, 1 error, 2 not initialized, 3 blocked by run budget/policy, 4 not found.")
    ap.add_argument("--version", action="version", version=f"research {research.__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    def add(parent, name, fn, help_, aliases=(), **defaults):
        # defaults: needs_project=False (command needs no project; called as fn(a)), takes_tail=True (takes a `-- cmd` tail)
        # help_=None: a hidden alias (argparse prints "==SUPPRESS==" for help=SUPPRESS on subparsers, so no help at all is used)
        sp = parent.add_parser(name, parents=[common], aliases=list(aliases), **({"help": help_} if help_ else {}))
        sp.set_defaults(fn=fn, **defaults)
        return sp

    groups = {}

    def group(name, help_, aliases=()):
        if name not in groups:          # idempotent: several modules add subcommands to one group (`agent`)
            g = sub.add_parser(name, help=help_, parents=[common], aliases=list(aliases))
            groups[name] = g.add_subparsers(dest="sub", metavar="<action>")
            g.set_defaults(fn=None, _group=g)
        return groups[name]

    for mod in (project, records, plans, supervisor, decide, chat, agents, skills, guide, site, hpc, git, profile, heal, job):
        mod.attach(sub, add, group)
    return ap


def _proj(a, sync: bool = True) -> Project:
    auth = detect_author(a.agent, a.model, a.as_) if (a.agent or a.model or a.as_) else None
    if a.root:
        return Project(a.root, author=auth, sync=sync)
    return Project.find(author=auth, sync=sync)


def _is_readonly(a) -> bool:
    """True when this invocation must not mutate the project: no hand-edit import, no run reconciliation,
    no Slurm polling, no event log. `report --full` and `upgrade --dry-run` depend on this."""
    return bool(getattr(a, "full", False) or getattr(a, "dry_run_readonly", False))


def _needs_project(a) -> bool:
    need = getattr(a, "needs_project", True)
    return need(a) if callable(need) else need


def _run(a, fn) -> int:
    if not _needs_project(a):
        return fn(a)
    p = _proj(a, sync=not _is_readonly(a))
    try:
        rc = fn(a, p)
        if fn.__name__ in MUTATING:
            try:
                p.refresh_context()
            except Exception:
                pass
        return rc or 0
    finally:
        p.close()


def _fail(a, e: ResearchError) -> int:
    if getattr(a, "json", False):
        print(json.dumps({"error": str(e), "hint": e.hint, "code": e.exit_code}), file=sys.stderr)
    else:
        print(red("✗ ") + str(e), file=sys.stderr)
        if e.hint:
            print(dim("  " + e.hint), file=sys.stderr)
    return e.exit_code


def _split_tail(argv):
    """Everything after `--` is the command a run/hpc subcommand executes."""
    if "--" not in argv:
        return argv, None
    i = argv.index("--")
    return argv[:i], argv[i + 1:]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `research checkpoint` with no action (or only flags) → create
    if argv and argv[0] in ("checkpoint", "cp") and (len(argv) == 1 or argv[1].startswith("-")) \
            and not any(x in ("-h", "--help") for x in argv[1:]):
        argv.insert(1, "create")
    ap = build_parser()
    argv, tail = _split_tail(argv)
    a = ap.parse_args(argv)
    if tail is not None:
        if hasattr(a, "cmd") and isinstance(getattr(a, "cmd", None), list) or getattr(a, "takes_tail", False):
            a.cmd = list(getattr(a, "cmd", None) or []) + tail
        else:
            ap.error("unexpected arguments after --")
    # A subcommand was chosen iff the parser set `fn` or `_group`. (`a.cmd` cannot be used for this:
    # the run/hpc subparsers define their own `cmd` positional, which shadows the top-level dest.)
    if not hasattr(a, "fn") and not hasattr(a, "_group"):
        ap.print_help()
        return 0
    fn = getattr(a, "fn", None)
    if fn is None:
        a._group.print_help()
        return 0
    try:
        return _run(a, fn)
    except ResearchError as e:
        return _fail(a, e)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
