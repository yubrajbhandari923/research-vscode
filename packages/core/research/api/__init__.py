"""The operation layer: every operation is defined ONCE here and served by every surface.

    @op(mutates=True)
    def create(p, type, **kw): ...

`rpc.py` (VS Code), `mcp.py` (agents), the TUI and (later) chat are thin transports over `call()`. An op adapts arguments and shapes the
return value; the logic lives in core modules. Governance stays in core (`governance`, `approval`, `supervisor`): the surface tags
(`chat=`, `mcp=`) are presentation filters that fail early, they never replace a gate.

Handlers take the open `Project` first (`needs_project=True`, the default) or the `Session` (`needs_project=False`: root and author only,
for ops that run before a project exists or open their own read-only snapshot). Both have `.root` and `.author`.
"""
from __future__ import annotations

import inspect
import os
from typing import Any, Callable, Dict, List, Optional

from ..store import Project
from ..util import NotInitialized, PolicyBlocked, ResearchError, detect_author


class BadParams(ResearchError):
    """The call shape does not fit the operation (only a binding failure; a TypeError inside a handler is a real error)."""
    exit_code = 2
    rpc_code = -32602

    def __init__(self, op: str, detail: str):
        super().__init__(f"bad parameters for {op}: {detail}")
        self.detail = detail


class UnknownOp(ResearchError):
    exit_code = 2
    rpc_code = -32601


class Op:
    """chat: None (not available from chat) | "read" (viewer and up) | "decide" (operator; core still enforces governance).
    mcp: the MCP tool name when agents may call it (descriptions and schemas are hand-written in mcp.py).
    mutates: may change project state (refreshes context/current.md after the call; the MCP readOnlyHint is its negation).
    A parameter called `dry_run` is the preview convention: surfaces that confirm first call with dry_run=True."""
    __slots__ = ("name", "fn", "sync", "needs_init", "needs_project", "mutates", "chat", "mcp", "params", "open")

    def __init__(self, name, fn, sync, needs_init, needs_project, mutates, chat, mcp):
        self.name, self.fn, self.sync, self.needs_init, self.needs_project = name, fn, sync, needs_init, needs_project
        self.mutates, self.chat, self.mcp = mutates, chat, mcp
        ps = list(inspect.signature(fn).parameters.values())[1:]
        self.params = {x.name: x.default is inspect.Parameter.empty for x in ps if x.kind not in (x.VAR_KEYWORD, x.VAR_POSITIONAL)}  # name -> required
        self.open = any(x.kind == x.VAR_KEYWORD for x in ps)

    @property
    def agent(self) -> bool:
        return self.mcp is not None

    @property
    def dry_run(self) -> bool:
        return "dry_run" in self.params


OPS: Dict[str, Op] = {}


def op(name: Optional[str] = None, *, sync: bool = True, needs_init: bool = True, needs_project: bool = True,
       mutates: bool = False, chat: Optional[str] = None, mcp: Optional[str] = None):
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        n = name or fn.__name__
        assert n not in OPS, f"duplicate op {n}"
        OPS[n] = Op(n, fn, sync, needs_init, needs_project, mutates, chat, mcp)
        return fn
    return deco


class Session:
    """The long-lived handle factory for one project root (one per RPC process, MCP server, TUI)."""

    def __init__(self, root: str, author: Optional[Dict[str, Any]] = None):
        self.root = os.path.abspath(root)
        self.p: Optional[Project] = None
        self.author = author or detect_author(author_type="human")   # actions from a UI are the human's
        self.changed = False                                          # did the last call write to the database

    def project(self, sync: bool = True) -> Project:
        if self.p is None:
            self.p = Project(self.root, author=self.author, sync=False)
        if sync:
            self.p.config = self.p.load_config()
            if not os.path.exists(self.p.db_path):   # deleted underneath us: rebuild
                self.p.close()
                self.p = Project(self.root, author=self.author, sync=False)
            self.p.sync()
            self.p.dirty = False                      # an index sync is not this call's write
        self.p.author = self.author
        return self.p

    def initialized(self) -> bool:
        from .. import workspace
        return workspace.locate(self.root) is not None

    def compat(self) -> Optional[str]:
        """None for a usable project (or none yet); otherwise why it cannot be opened."""
        from .. import store, workspace
        loc = workspace.locate(self.root)
        if not loc:
            return None
        try:
            store.check_format(os.path.join(loc.ledger_root, store.RESEARCH_DIR))
        except ResearchError as e:
            return f"{e} {e.hint or ''}".strip()
        return None


def ro(x: Any) -> Project:
    """A read-only snapshot handle (no sync, so rendering mutates nothing). The caller closes it."""
    return Project(x.root, author=x.author, sync=False)


def call(name: str, session: Session, params: Optional[Dict[str, Any]] = None, surface: str = "rpc",
         author: Optional[Dict[str, Any]] = None) -> Any:
    """Run one op. `surface`: rpc | mcp | tui | chat. `author`: per-request override (restored afterwards).
    Afterwards `session.changed` says whether the call committed to the database (the extension refreshes its tree on it)."""
    params = dict(params or {})
    o = OPS.get(name)
    if o is None:
        raise UnknownOp(f"unknown operation {name}")
    if surface == "chat" and o.chat is None:
        raise PolicyBlocked(f"{name} is not available from chat", hint="Use the terminal or VS Code.")
    if o.needs_init and not session.initialized():
        raise NotInitialized("Project not initialized", hint="Run 'Research: Initialize Project'.")
    saved = session.author
    if author is not None:
        session.author = detect_author(**{k: v for k, v in author.items() if k in ("agent", "model", "author_type")})
    session.changed = ok = False
    try:
        proj = session.project(sync=o.sync or surface == "mcp") if (o.needs_project or surface == "mcp") else None
        target = proj if o.needs_project else session
        try:
            inspect.signature(o.fn).bind(target, **params)
        except TypeError as e:
            raise BadParams(name, str(e))
        out = o.fn(target, **params)
        ok = True
        return out
    finally:
        session.author = saved
        p = session.p
        if p is not None:
            p.author = saved
            session.changed = bool(p.dirty)
            if p.dirty or (ok and o.mutates):
                try:
                    p.refresh_context()
                except Exception:
                    pass


from . import records, runs, plans, guide, site, hpc, agents, settings, agent_tools, chat, decisions  # noqa: E402,F401  (populate OPS)
