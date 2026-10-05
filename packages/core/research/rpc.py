"""Line-delimited JSON-RPC over stdio, used by the VS Code extension.

    python -m research.rpc --root /path/to/project

Request:  {"id": 1, "method": "resume", "params": {...}}
Response: {"id": 1, "result": ...}  |  {"id": 1, "error": {"message", "hint", "code"}}

The extension spawns one process per workspace folder. It runs wherever the extension
host runs — under Remote SSH that is the remote machine, next to the files.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, Optional

from . import __version__
from . import context as C
from . import runs as R
from . import services as S
from . import views as V
from .schema import ENTITIES, STATUSES, normalize_id
from .store import Project, find_root
from .util import NotInitialized, ResearchError, detect_author, human_size


class Server:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.p: Optional[Project] = None
        self.author = detect_author(author_type="human")  # actions from the UI are the human's

    # ------------------------------------------------------------------ project handle
    def project(self, sync: bool = True) -> Project:
        if self.p is None:
            self.p = Project(self.root, author=self.author, sync=False)
        if sync:
            self.p.config = self.p.load_config()
            if not os.path.exists(self.p.db_path):  # deleted underneath us → rebuild
                self.p.close()
                self.p = Project(self.root, author=self.author, sync=False)
            self.p.sync()
        return self.p

    def initialized(self) -> bool:
        return os.path.isfile(os.path.join(self.root, ".research", "config.yaml"))

    # ------------------------------------------------------------------ methods
    def m_ping(self) -> Dict[str, Any]:
        return {"version": __version__, "root": self.root, "initialized": self.initialized(),
                "python": sys.executable, "pid": os.getpid()}

    def m_init(self, name: Optional[str] = None, goal: Optional[str] = None) -> Dict[str, Any]:
        self.p = Project.init(self.root, name=name, goal=goal)
        self.p.author = self.author
        return {"root": self.root, "name": self.p.name}

    def m_resume(self) -> Dict[str, Any]:
        return C.resume_context(self.project())

    def m_tree(self) -> Dict[str, Any]:
        """Everything the sidebar trees need, in one round-trip."""
        p = self.project()
        qs = p.list("question", order="id")
        exps = p.list("experiment", order="id DESC")
        runs = p.q("SELECT id, experiment_id, status, label, command, exit_code, backend, slurm_job_id, started_at, "
                   "ended_at, parameters, author_type, author_name FROM runs ORDER BY id")
        runs_by: Dict[str, list] = {}
        for r in runs:
            r["parameters"] = json.loads(r["parameters"] or "{}")
            runs_by.setdefault(r["experiment_id"], []).append(r)
        arts = R.list_artifacts(p)
        arts_by_run: Dict[str, list] = {}
        arts_by_exp: Dict[str, list] = {}
        for a in arts:
            a["size_h"] = human_size(a.get("size"))
            if a.get("run_id"):
                arts_by_run.setdefault(a["run_id"], []).append(a)
            elif a.get("experiment_id"):
                arts_by_exp.setdefault(a["experiment_id"], []).append(a)
        for e in exps:
            e["state"] = S.experiment_state(p, e["id"])
            e["runs"] = runs_by.get(e["id"], [])
            for r in e["runs"]:
                r["artifacts"] = arts_by_run.get(r["id"], [])
            e["artifacts"] = arts_by_exp.get(e["id"], [])
        return {
            "project": {"name": p.name, "goal": p.config["project"].get("goal")},
            "questions": qs, "experiments": exps,
            "findings": p.list("finding", order="updated_at DESC"),
            "decisions": p.list("decision", order="date DESC, id DESC"),
            "checkpoints": p.list("checkpoint", order="created_at DESC, id DESC"),
            "notes": S.list_notes(p), "artifacts": arts,
            "skills": S.list_skills(p),
            "agent": {k: S.list_agent_files(p, k) for k in ("context", "prompts", "templates")},
            "needs_synthesis": [e["id"] for e in S.experiments_needing_synthesis(p)],
        }

    def m_index(self) -> Dict[str, Any]:
        """Id/title list for pickers."""
        p = self.project(sync=False)
        out = []
        for et, spec in ENTITIES.items():
            cols = "id, title, status" if et in STATUSES else "id, title, NULL AS status"
            for r in p.q(f"SELECT {cols} FROM {spec['table']} ORDER BY id"):
                out.append({"id": r["id"], "type": et, "title": r["title"], "status": r["status"]})
        for r in p.q("SELECT id, label, command, status, experiment_id FROM runs ORDER BY id"):
            out.append({"id": r["id"], "type": "run", "title": r["label"] or (r["command"] or "")[:60],
                        "status": r["status"], "experiment_id": r["experiment_id"]})
        for r in p.q("SELECT id, name, path FROM artifacts ORDER BY id"):
            out.append({"id": r["id"], "type": "artifact", "title": r["name"], "path": r["path"]})
        return {"items": out, "statuses": STATUSES}

    def m_show(self, id: str) -> Dict[str, Any]:
        return V.show(self.project(), id if id.startswith("note:") else normalize_id(id))

    def m_config(self) -> Dict[str, Any]:
        p = self.project(sync=False)
        return p.load_config()

    def m_update_project(self, **kw: Any) -> Dict[str, Any]:
        return self.project(sync=False).update_project(**kw)

    # creation / edits (all delegate to services) ---------------------------------------
    def m_create(self, type: str, **kw: Any) -> Dict[str, Any]:
        p = self.project()
        fn: Dict[str, Callable[..., Any]] = {
            "question": S.create_question, "experiment": S.create_experiment, "variant": S.create_variant,
            "finding": S.create_finding, "decision": S.create_decision, "checkpoint": C.create_checkpoint,
            "note": S.create_note, "skill": S.create_skill,
        }
        if type not in fn:
            raise ResearchError(f"cannot create {type!r}")
        return fn[type](p, **kw)

    def m_update(self, type: str, id: str, **kw: Any) -> Dict[str, Any]:
        p = self.project()
        fn = {"question": S.update_question, "experiment": S.update_experiment, "finding": S.update_finding,
              "decision": S.update_decision}.get(type)
        if not fn:
            raise ResearchError(f"cannot update {type!r}")
        return fn(p, id, **kw)

    def m_set_status(self, id: str, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return S.set_status(self.project(), id, status, reason)

    def m_set_baseline(self, id: Optional[str] = None) -> Any:
        return S.set_baseline(self.project(), id)

    def m_synthesize(self, id: str, **kw: Any) -> Dict[str, Any]:
        return S.synthesize(self.project(), id, **kw)

    def m_supersede_finding(self, id: str, by: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        return S.supersede_finding(self.project(), id, by, reason)

    def m_duplicate_skill(self, src: str, name: str) -> Dict[str, Any]:
        return S.duplicate_skill(self.project(sync=False), src, name)

    def m_pin_note(self, id: str, pinned: bool = True) -> Dict[str, Any]:
        return S.pin_note(self.project(sync=False), id, pinned)

    def m_link_note(self, id: str, targets: Any, remove: bool = False) -> Dict[str, Any]:
        return S.link_note(self.project(sync=False), id, targets, remove)

    def m_checkpoint_draft(self) -> Dict[str, Any]:
        return C.checkpoint_draft(self.project())

    def m_experiment_state(self, id: str) -> Dict[str, Any]:
        return S.experiment_state(self.project(), normalize_id(id, "EXP"))

    def m_check_run_budget(self, experiment: str) -> Dict[str, Any]:
        p = self.project()
        st = S.experiment_state(p, normalize_id(experiment, "EXP"))
        msgs = []
        if st["over_run_budget"]:
            msgs.append(f"{st['unsynthesized']} unsynthesized runs (limit {st['max_runs_without_synthesis']})")
        if st["over_failure_budget"]:
            msgs.append(f"{st['unreviewed_failed']} failed runs without review (limit {st['max_failed_runs_without_review']})")
        return {"ok": not msgs, "messages": msgs, "state": st}

    # runs ------------------------------------------------------------------------------
    def m_run_attach(self, experiment: str, **kw: Any) -> Dict[str, Any]:
        return R.attach_run(self.project(), experiment, **kw)

    def m_run_start(self, experiment: str, command: str, backend: str = "local", **kw: Any) -> Dict[str, Any]:
        p = self.project()
        if backend == "slurm":
            slurm = kw.pop("slurm", None) or {}
            return R.submit_run(p, experiment, command, **kw, **slurm)
        return R.exec_run(p, experiment, command, detach=True, **kw)

    def m_run_create(self, experiment: str, **kw: Any) -> Dict[str, Any]:
        """Register a run to be executed in a VS Code terminal via `research run exec`."""
        return R.create_run(self.project(), experiment, **kw)

    def m_run_cancel(self, id: str) -> Dict[str, Any]:
        return R.cancel_run(self.project(), id)

    def m_run_update(self, id: str, **kw: Any) -> Dict[str, Any]:
        return R.update_run(self.project(), id, **kw)

    def m_run_refresh(self, id: Optional[str] = None) -> Any:
        p = self.project(sync=False)
        if id:
            return R.reconcile_run(p, id, force=True)
        R.sync_runs(p, slurm_min_interval=0)
        return {"ok": True}

    def m_register_artifact(self, path: str, **kw: Any) -> Dict[str, Any]:
        return R.register_artifact(self.project(), path, **kw)

    def m_log_metric(self, name: str, value: Any, **kw: Any) -> Dict[str, Any]:
        return R.log_metric(self.project(), name, value, **kw)

    # maintenance -------------------------------------------------------------------------
    def m_write_context(self) -> Dict[str, Any]:
        return {"path": C.write_current_md(self.project())}

    def m_agents_md(self) -> Dict[str, Any]:
        return {"path": self.project(sync=False).write_agents_md()}

    def m_sync(self) -> Dict[str, Any]:
        return self.project(sync=False).sync()

    def m_rebuild(self) -> Dict[str, Any]:
        return self.project(sync=False).rebuild_index(backup=True)

    def m_events(self, limit: int = 50) -> Any:
        return self.project(sync=False).q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),))


MUTATING = {"create", "update", "set_status", "set_baseline", "synthesize", "supersede_finding", "run_attach",
            "run_start", "run_create", "run_cancel", "run_update", "register_artifact", "log_metric", "init",
            "update_project", "sync", "rebuild"}


def serve(root: str, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    srv = Server(root)

    def send(obj: Dict[str, Any]) -> None:
        stdout.write(json.dumps(obj, default=str, ensure_ascii=False) + "\n")
        stdout.flush()

    send({"event": "ready", "version": __version__, "root": srv.root, "initialized": srv.initialized()})
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send({"id": None, "error": {"message": "invalid JSON", "code": -32700}})
            continue
        rid, method, params = req.get("id"), req.get("method", ""), req.get("params") or {}
        if "author" in params:  # optional author override from the UI
            srv.author = detect_author(**{k: v for k, v in (params.pop("author") or {}).items()
                                          if k in ("agent", "model", "author_type")})
            if srv.p:
                srv.p.author = srv.author
        fn = getattr(srv, "m_" + method, None)
        if fn is None:
            send({"id": rid, "error": {"message": f"unknown method {method}", "code": -32601}})
            continue
        try:
            if method not in ("ping", "init") and not srv.initialized():
                raise NotInitialized("Project not initialized", hint="Run 'Research: Initialize Project'.")
            result = fn(**params)
            if method in MUTATING and srv.p is not None:
                try:
                    C.write_current_md(srv.p)
                except Exception:
                    pass
            send({"id": rid, "result": result})
        except ResearchError as e:
            send({"id": rid, "error": {"message": str(e), "hint": e.hint, "code": e.exit_code,
                                       "kind": type(e).__name__}})
        except TypeError as e:
            send({"id": rid, "error": {"message": f"bad parameters for {method}: {e}", "code": -32602}})
        except Exception as e:  # pragma: no cover
            send({"id": rid, "error": {"message": f"{type(e).__name__}: {e}", "code": -32603,
                                       "trace": traceback.format_exc(limit=6)}})


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m research.rpc")
    ap.add_argument("--root", default=None)
    a = ap.parse_args(argv)
    root = a.root or find_root() or os.getcwd()
    serve(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
