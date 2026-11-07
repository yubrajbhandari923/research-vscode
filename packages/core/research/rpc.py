"""Line-delimited JSON-RPC over stdio, used by the VS Code extension.

    python -m research.rpc --root /path/to/project

Request:  {"id": 1, "method": "resume", "params": {...}}
Response: {"id": 1, "result": ...}  |  {"id": 1, "error": {"message", "hint", "code"}}

The extension spawns one process per workspace folder. It runs wherever the extension
host runs — under Remote SSH that is the remote machine, next to the files.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

from . import __version__

PROTOCOL = 1
from . import context as C
from . import plans as P
from . import skills as SK
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
        from . import workspace
        return workspace.locate(self.root) is not None

    # ------------------------------------------------------------------ methods
    def _compat(self) -> Optional[str]:
        """None when the project is a v2 project (or there is none yet); otherwise the reason it cannot be opened."""
        from . import store, workspace
        loc = workspace.locate(self.root)
        if not loc:
            return None
        try:
            store.check_format(os.path.join(loc.ledger_root, store.RESEARCH_DIR))
        except ResearchError as e:
            return f"{e} {e.hint or ''}".strip()
        return None

    def m_ping(self) -> Dict[str, Any]:
        ok = self.initialized() and not self._compat()
        return {"version": __version__, "protocol": PROTOCOL, "root": self.root, "initialized": self.initialized(), "incompatible": self._compat(),
                "name": self.project(sync=False).name if ok else os.path.basename(self.root), "python": sys.executable, "pid": os.getpid()}

    def m_init(self, name: Optional[str] = None, goal: Optional[str] = None) -> Dict[str, Any]:
        self.p = Project.init(self.root, name=name, goal=goal)
        self.p.author = self.author
        return {"root": self.root, "name": self.p.name}

    def m_control(self, page: str) -> Dict[str, Any]:
        from . import control
        fn = {"home": control.home, "work": control.work, "research": control.research}.get(page)
        if not fn:
            raise ResearchError(f"unknown control page {page!r}")
        return fn(self.project(sync=False))

    def m_sandbox_preview(self, experiment: Optional[str] = None, task: Optional[str] = None, profile: Optional[str] = None,
                          datasets: Optional[List[str]] = None) -> Dict[str, Any]:
        from . import sandbox as SB
        return SB.preview(self.project(sync=False), experiment=experiment, task=task, profile_name=profile, datasets=datasets)

    def m_promotions(self, closed: bool = False) -> List[Dict[str, Any]]:
        from . import promotion
        return promotion.list_candidates(self.project(sync=False), closed)

    def m_promotion_diff(self, id: str) -> Dict[str, Any]:
        from . import promotion
        return promotion.diff(self.project(sync=False), id)

    def m_promotion_action(self, id: str, action: str, note: Optional[str] = None, confirm: bool = False,
                           verdict: Optional[str] = None, command: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
        """verify | review | approve | integrate | abandon. The core refuses agents and unconfirmed integration."""
        from . import promotion as PR
        p = self.project()
        if action == "verify":
            return PR.verify(p, id, command)
        if action == "review":
            return PR.review(p, id, verdict or "comment", note)
        if action == "approve":
            return PR.approve(p, id, note, pin)
        if action == "integrate":
            return PR.integrate(p, id, confirm, pin)
        if action == "abandon":
            return PR.abandon(p, id, note)
        raise ResearchError(f"unknown promotion action {action!r}")

    def m_doctor(self, fix: Optional[str] = None) -> Dict[str, Any]:
        """The Project Doctor. Without `fix` it is read-only; a fix touches only generated files."""
        from . import doctor
        return doctor.apply_fix(self.root, fix) if fix else doctor.run(self.root)

    def m_new_experiment_preview(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        from . import services
        return services.new_experiment_plan(self.project(sync=False), spec)

    def m_new_experiment(self, spec: Dict[str, Any], pin: Optional[str] = None) -> Dict[str, Any]:
        from . import governance, services
        p = self.project()
        governance.confirm_human_experiment(p, pin)     # only asks when the optional setting is on
        return services.new_experiment(p, spec)

    def m_settings(self, category: Optional[str] = None, search: Optional[str] = None) -> Dict[str, Any]:
        from . import registry
        return registry.describe(self.project(sync=False), category, search)

    def m_settings_preview(self, changes: Dict[str, Any], scope: str = "project", remove: bool = False,
                           expected: Optional[str] = None) -> Dict[str, Any]:
        from . import registry
        return registry.preview(self.project(sync=False), changes, scope, remove, expected)

    def m_settings_apply(self, changes: Dict[str, Any], scope: str = "project", reason: Optional[str] = None,
                         confirm: bool = False, remove: bool = False, expected: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
        from . import registry
        return registry.apply(self.project(), changes, scope, reason, confirm, remove, expected, pin)

    def m_workspace_info(self) -> Dict[str, Any]:
        from . import workspace as W
        return W.info(self.project(sync=False))

    def m_workspaces(self, archived: bool = False) -> List[Dict[str, Any]]:
        from . import workspace as W
        return W.list_workspaces(self.project(sync=False), include_archived=archived)

    def m_workspace_create(self, experiment: str, name: Optional[str] = None, base: Optional[str] = None) -> Dict[str, Any]:
        from . import workspace as W
        return W.create(self.project(), experiment, name=name, base=base)

    def m_workspace_archive(self, id: str, dry_run: bool = True, reason: Optional[str] = None) -> Dict[str, Any]:
        """Archival removes a directory, so the impact preview is the default."""
        from . import workspace as W
        p = self.project()
        return W.archive_impact(p, id) if dry_run else W.archive(p, id, reason=reason)

    def m_resume(self) -> Dict[str, Any]:
        return C.resume_context(self.project())

    def m_home(self) -> Dict[str, Any]:
        """Research Home aggregation - primary human landing page data."""
        return C.research_home(self.project())

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

        # Plans with their tasks (for sidebar)
        plans = P.list_plans(p)
        for plan in plans:
            plan["tasks"] = P.list_tasks(p, plan_id=plan["id"])

        return {
            "project": {"name": p.name, "goal": p.config["project"].get("goal")},
            "questions": qs, "experiments": exps,
            "findings": p.list("finding", order="updated_at DESC"),
            "decisions": p.list("decision", order="date DESC, id DESC"),
            "checkpoints": p.list("checkpoint", order="created_at DESC, id DESC"),
            "notes": S.list_notes(p), "artifacts": arts,
            "skills": SK.list_skills(p),
            "agent": {k: S.list_agent_files(p, k) for k in ("context", "prompts", "templates")},
            "needs_synthesis": [e["id"] for e in S.experiments_needing_synthesis(p)],
            "plans": plans,
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
            "plan": P.create_plan, "task": P.create_task,
        }
        if type not in fn:
            raise ResearchError(f"cannot create {type!r}")
        pin = kw.pop("pin", None)
        if type == "experiment":
            from . import governance
            governance.confirm_human_experiment(p, pin)
        return fn[type](p, **kw)

    def m_update(self, type: str, id: str, **kw: Any) -> Dict[str, Any]:
        p = self.project()
        fn = {"question": S.update_question, "experiment": S.update_experiment, "finding": S.update_finding,
              "decision": S.update_decision, "plan": P.update_plan, "task": P.update_task}.get(type)
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

    def m_run_cancel(self, id: str) -> Dict[str, Any]:
        return R.cancel_run(self.project(), id)

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

    def m_sync(self) -> Dict[str, Any]:
        return self.project(sync=False).sync()

    def m_rebuild(self) -> Dict[str, Any]:
        return self.project(sync=False).rebuild_index(backup=True)

    def m_events(self, limit: int = 50) -> Any:
        return self.project(sync=False).q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),))

    # plans / tasks ----------------------------------------------------------------------
    def m_task_context(self, id: str) -> Dict[str, Any]:
        return P.task_context(self.project(), id)

    def m_task_brief(self, id: str) -> str:
        """The task context as Markdown (what an agent reads)."""
        from . import agents as AG
        return AG.task_brief_md(P.task_context(self.project(), id))

    def m_release_task(self, id: str, note: Optional[str] = None) -> Dict[str, Any]:
        return P.release_task(self.project(), id, note)

    def m_task_note(self, id: str, text: str) -> Dict[str, Any]:
        return P.add_task_note(self.project(), id, text)

    def m_verify_task(self, id: str) -> Dict[str, Any]:
        from . import verification as VF
        return VF.verify_task(self.project(), id)

    def m_review_finding(self, id: str, verdict: str, notes: Optional[str] = None,
                         confidence: Optional[str] = None) -> Dict[str, Any]:
        from . import verification as VF
        return VF.review_finding(self.project(), id, verdict, notes, confidence)

    # search / compare / report ------------------------------------------------------------
    def m_search(self, query: str, type: Any = None, limit: int = 30) -> Any:
        from .search import search
        return search(self.project(), query, type, limit)

    def m_compare_runs(self, a: str, b: str) -> Dict[str, Any]:
        from . import compare as CMP
        return CMP.compare_runs(self.project(), a, b)

    def m_report(self, scope: Optional[str] = None, fmt: str = "both") -> Dict[str, Any]:
        from . import report as RP
        return RP.write_report(self.project(), scope, fmt)

    def _ro(self):
        return Project(self.root, author=self.author, sync=False)

    def m_guide_status(self) -> Dict[str, Any]:
        """Scientist Guide: important results, chapters' freshness, citations, last draft check, review state, Quarto availability. Read-only."""
        from . import guide as G
        p = self._ro()
        try:
            return G.overview(p)
        finally:
            p.close()

    def m_guide_init(self, workflow: Optional[bool] = None) -> Dict[str, Any]:
        from . import guide as G
        return G.init(self.project(), workflow=workflow)

    def m_guide_build(self, edition: str = "scientific", pdf: bool = False, allow_invalid: bool = False) -> Dict[str, Any]:
        from . import guide_quarto as GQ
        return GQ.build(self.project(), edition, pdf, allow_invalid)

    def m_guide_preview(self, edition: str = "scientific") -> Dict[str, Any]:
        """The command that serves the book with live reload (the extension runs it in a terminal). Starts nothing itself."""
        from . import guide_quarto as GQ
        from . import provenance as PV
        p = self.project()
        return {"argv": GQ.preview_argv(p, edition), "cwd": PV.main_root(p)}

    def m_guide_highlight(self, id: str, on: bool = True) -> Dict[str, Any]:
        from . import guide as G
        return G.star(self.project(), id, on)

    def m_guide_log(self, ids: Optional[List[str]] = None) -> Dict[str, Any]:
        from . import guide as G
        return G.log(self.project(), ids)

    def m_guide_draft(self, results: Optional[List[str]] = None, section: Optional[str] = None, profile: Optional[str] = None,
                      model: Optional[str] = None) -> Dict[str, Any]:
        """Prepare (never start) a bounded agent draft. Returns the dispatch (argv/env/brief) the extension then previews and starts."""
        from . import guide as G
        d = G.draft(self.project(), profile, model, None, results, [section] if section else None)
        d["shell"] = None   # never ship a copy-pasteable string built from record text
        return d

    def m_guide_draft_done(self, ids: Optional[List[str]] = None) -> Dict[str, Any]:
        from . import guide as G
        return G.draft_done(self.project(), ids)

    def m_guide_cite_list(self) -> Dict[str, Any]:
        from . import guide as G
        p = self._ro()
        try:
            return G.cite_list(p)
        finally:
            p.close()

    def m_guide_cite_add(self, bibtex: Optional[str] = None, key: Optional[str] = None, title: Optional[str] = None,
                         authors: Optional[str] = None, year: Optional[str] = None, doi: Optional[str] = None,
                         journal: Optional[str] = None, url: Optional[str] = None) -> Dict[str, Any]:
        from . import guide as G
        return G.cite_add(self.project(), bibtex, key, title, authors, year, doi, journal, url)

    def m_guide_validate(self) -> Dict[str, Any]:
        """Deterministic claim/figure/section provenance validation. Read-only; not a scientific review."""
        from . import provenance as PV
        p = Project(self.root, author=self.author, sync=False)
        try:
            return PV.validate(p)
        finally:
            p.close()

    def m_reports(self) -> Dict[str, Any]:
        """Reports already written under .research/reports (newest first), with absolute paths for the editor. Read-only."""
        p = self.project(sync=False)
        d = p.path("reports")
        rows = []
        for f in (os.listdir(d) if os.path.isdir(d) else []):
            if f.endswith((".md", ".html")):
                st = os.stat(os.path.join(d, f))
                rows.append({"name": f, "abs": os.path.join(d, f), "bytes": st.st_size, "modified": st.st_mtime, "full": f.endswith("-full.md")})
        return {"dir": d, "reports": sorted(rows, key=lambda r: -r["modified"])}

    def m_report_full(self, scope: Optional[str] = None, check: bool = True) -> Dict[str, Any]:
        """Deterministic full context export. Read-only: opened without sync so nothing is mutated."""
        from . import fullreport as FR
        p = Project(self.root, author=self.author, sync=False)
        try:
            return FR.write(p, scope, check=check)
        finally:
            p.close()

    def m_agents_md_state(self) -> Dict[str, Any]:
        """The managed-block action, the block itself and a unified diff. Touches no file."""
        from . import agentsmd as AM
        p = self.project(sync=False)
        st = AM.state(p)
        out = {k: st[k] for k in ("path", "action", "problem", "up_to_date", "new")}
        out["diff"] = "" if st["problem"] else AM.diff(p)
        return out

    def m_agents_md_apply(self) -> Dict[str, Any]:
        from . import agentsmd as AM
        return AM.apply(self.project(sync=False))

    def m_config_check(self) -> Dict[str, Any]:
        """Validate this project's config. Read-only."""
        from . import configfile
        from .store import DEFAULT_CONFIG
        p = self.project(sync=False)
        with open(p.config_path, encoding="utf-8") as f:
            text = f.read()
        return configfile.check(p.raw_config, DEFAULT_CONFIG, text)

    def m_config_show(self, effective: bool = False, sources: bool = False) -> Dict[str, Any]:
        """This project's own settings, every effective value, or per-key provenance."""
        from . import configfile
        from .store import DEFAULT_CONFIG
        p = self.project(sync=False)
        if sources:
            return {"sources": configfile.sources(p.raw_config, DEFAULT_CONFIG),
                    "machine_config": configfile.machine_path(),
                    "project_config": p.rel(p.config_path)}
        return {"settings": p.config if effective else p.raw_config, "effective": effective}

    def m_hpc_status(self) -> Dict[str, Any]:
        """HPC overview for the Run view: configured clusters plus every run with remote state."""
        from .hpc import config as HC
        p = self.project()
        runs = []
        for r in p.q("SELECT id, experiment_id, status, slurm_job_id, backend, slurm FROM runs "
                     "WHERE backend IN ('slurm','hpc-bundle') ORDER BY id DESC LIMIT 200"):
            hpc = ((json.loads(r["slurm"] or "{}") or {}).get("hpc")) or {}
            runs.append({"id": r["id"], "experiment_id": r["experiment_id"], "status": r["status"],
                         "slurm_job_id": r["slurm_job_id"], "backend": r["backend"],
                         "cluster": hpc.get("cluster"), "hpc_state": hpc.get("state"),
                         "remote_run_dir": hpc.get("remote_run_dir"),
                         "collected_at": hpc.get("collected_at")})
        return {"clusters": HC.names(p), "default_cluster": HC.default_name(p), "runs": runs,
                "enabled": bool(HC.names(p))}

    def _exp_project(self, experiment: str):
        """The Project rooted at the experiment's worktree: a bundle must ship the experiment's code, not main's."""
        from . import workspace as W
        from .schema import normalize_id
        p = self.project()
        exp = normalize_id(experiment, "EXP")
        for rec in W._fold(W._records(p)).values():
            if rec["status"] == "active" and rec.get("experiment") == exp:
                s = W.show(p, rec["id"])
                if s["present"]:
                    return Project(s["path"], author=self.author, sync=False)
        return p

    def m_hpc_init(self, cluster: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
        """Create the remote project directories (the preview is the default)."""
        from .hpc import remote as HR
        return HR.init_workspace(self.project(), cluster, dry_run=dry_run)

    def m_hpc_submit(self, experiment: str, command: str, cluster: Optional[str] = None, dry_run: bool = True,
                     approve: bool = False, resources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Submit one run of EXPERIMENT's worktree code to the cluster. Preview first; `approve` is the explicit go-ahead."""
        from .hpc import remote as HR
        if not command or not command.strip():
            raise ResearchError("A command to run is required")
        p = self._exp_project(experiment)
        try:
            return HR.submit(p, experiment, cluster_name=cluster, command=command.strip(), dry_run=dry_run,
                             approve=approve and not dry_run, resources=resources or {})
        finally:
            if p is not self.p:
                p.close()

    def m_hpc_sync(self, cluster: Optional[str] = None) -> Dict[str, Any]:
        from .hpc import remote as HR
        return HR.sync(self.project(), cluster_name=cluster)

    # supervisor ----------------------------------------------------------------------------
    def m_env_check(self, packages: Optional[List[str]] = None, python: Optional[str] = None) -> Dict[str, Any]:
        from . import envcheck
        p = self.project(sync=False)
        if python:                                   # test a candidate before it is saved
            saved = p.config.get("environment")
            p.config["environment"] = {"python": python, "packages": packages or []}
            try:
                return envcheck.check(p, packages)
            finally:
                p.config["environment"] = saved
        return envcheck.check(p, packages)

    def m_plan_yaml_template(self) -> Dict[str, Any]:
        from . import planspec as PS
        return {"text": PS.TEMPLATE}

    def m_plan_yaml_validate(self, text: str) -> Dict[str, Any]:
        from . import planspec as PS
        return PS.validate(text, self.project())

    def m_plan_yaml_diff(self, text: str) -> Dict[str, Any]:
        from . import planspec as PS
        return PS.diff(self.project(), text)

    def m_plan_yaml_apply(self, text: str, dry_run: bool = False) -> Dict[str, Any]:
        from . import planspec as PS
        return PS.apply(self.project(), text, dry_run=dry_run, source="ui")

    def m_plan_yaml_export(self, plan: str, experiment: Optional[str] = None, task: Optional[str] = None,
                           partial: bool = False) -> Dict[str, Any]:
        from . import planspec as PS
        return {"text": PS.export(self.project(), plan, scope="partial" if partial else "full", experiment=experiment, task=task)}

    def m_managed_plans(self) -> List[Dict[str, Any]]:
        """Plans that came through YAML, with their run state: the Control Center's Plans page."""
        from . import planspec as PS, supervisor as SV
        p = self.project(sync=False)
        out = []
        for st in PS.all_states(p):
            try:
                s = SV.status(p, st["plan_key"])
                out.append({"key": st["plan_key"], "id": st["plan_id"], "revision": st["revision"], "title": (st.get("spec") or {}).get("title"),
                            "state": s["state"], "health": s["health"], "progress": s["progress"], "counts": s["counts"], "pending_proposals": len(s["proposals"])})
            except ResearchError:
                continue
        return out

    def m_supervisor_status(self, plan: str) -> Dict[str, Any]:
        from . import supervisor as SV
        return SV.status(self.project(sync=False), plan)

    def m_supervisor_daemon(self, action: str = "status") -> Dict[str, Any]:
        from . import supervisor as SV
        p = self.project(sync=False)
        if action == "start":
            return SV.daemon_start(p, detach=True)
        if action == "stop":
            return SV.daemon_stop(p)
        if action == "reload":
            return SV.daemon_reload(p)
        if action == "restart":
            return SV.daemon_restart(p)
        return SV.daemon_status(p)

    # --- supervisor intervention, halt diagnosis, Telegram (intervene.py, notify.py)
    def m_interventions(self, task: Optional[str] = None) -> List[Dict[str, Any]]:
        from . import intervene as I
        return I.list_all(self.project(sync=False), task)

    def m_intervention_request(self, task: str, message: str, interrupt: bool = False) -> Dict[str, Any]:
        from . import intervene as I
        return I.request(self.project(), task, message, interrupt=interrupt, source="vscode")

    def m_intervention_action(self, id: str, action: str, note: Optional[str] = None, pin: Optional[str] = None) -> Dict[str, Any]:
        from . import intervene as I
        p = self.project()
        if action == "approve":
            return I.approve(p, id, note, pin)
        if action == "reject":
            return I.reject(p, id, note)
        if action == "cancel":
            return I.cancel(p, id)
        if action == "plan":
            return I.plan_it(p, id)
        raise ResearchError("action must be approve, reject, cancel or plan")

    def m_debug_run(self, plan: str) -> Dict[str, Any]:
        from . import notify
        return notify.run_debug(self.project(), plan)

    def m_debug_latest(self) -> Optional[Dict[str, Any]]:
        from . import notify
        return notify.latest_debug(self.project(sync=False))

    def m_telegram_status(self) -> Dict[str, Any]:
        from . import notify
        return notify.status(self.project(sync=False))

    def m_telegram_set(self, token: str, chat_id: Optional[str] = None, discover: bool = False) -> Dict[str, Any]:
        from . import notify
        if discover and not chat_id:
            chat_id = notify.discover_chat(token.strip(), 45)
            if chat_id is None:
                raise ResearchError("No message reached the bot in 45 seconds", hint="Open your bot in Telegram, press Start or send any message, then try again.")
        notify.set_credentials(token, chat_id)
        return notify.status(self.project(sync=False))

    def m_telegram_clear(self) -> Dict[str, Any]:
        from . import notify
        notify.clear_credentials()
        return notify.status(self.project(sync=False))

    def m_telegram_test(self) -> Dict[str, Any]:
        from . import notify
        return {"sent": notify.send("Research OS: Telegram is connected. Send /help for commands.")}

    def m_plan_control(self, plan: str, action: str, cancel_agents: bool = False) -> Dict[str, Any]:
        from . import supervisor as SV
        p = self.project()
        fn = {"run": lambda: SV.plan_run(p, plan), "pause": lambda: SV.plan_pause(p, plan), "resume": lambda: SV.plan_resume(p, plan),
              "stop": lambda: SV.plan_stop(p, plan, cancel_agents)}.get(action)
        if not fn:
            raise ResearchError(f"unknown plan action {action!r}")
        return fn()

    def m_supervisor_task(self, task: str, action: str, note: Optional[str] = None, reason: Optional[str] = None,
                          attempts: Optional[List[int]] = None) -> Dict[str, Any]:
        from . import supervisor as SV
        p = self.project()
        if action == "approve":
            return SV.approve_task(p, task, note)
        if action == "accept":
            return SV.accept(p, task, reason or "")
        if action == "void":
            return SV.void_attempts(p, task, note or reason or "attempts voided by a human", attempts)
        if action == "continue":
            return SV.continue_with(p, task, reason or "", note)
        if action in ("failed", "completed", "retry", "unblock"):
            return SV.resolve(p, task, action, note)
        raise ResearchError(f"unknown task action {action!r}")

    def m_provider_status(self, task: Optional[str] = None) -> Dict[str, Any]:
        from . import supervisor as SV
        return SV.provider_status(self.project(sync=False), task)

    def m_proposals(self) -> List[Dict[str, Any]]:
        from . import supervisor as SV
        return SV.list_proposals(self.project(sync=False))

    def m_proposal_decide(self, id: str, approve: Any, note: Optional[str] = None, pin: Optional[str] = None, rev: Optional[int] = None) -> Dict[str, Any]:
        """approve: true | false | "revise". Approving needs the PIN (when one is set)."""
        from . import supervisor as SV
        return SV.decide_proposal(self.project(), id, approve, note, pin, rev)

    def m_governance(self) -> Dict[str, Any]:
        from . import approval, governance
        p = self.project(sync=False)
        return {"policy": governance.cfg(p), "pin": approval.status(), "proposals": [x for x in __import__("research.supervisor", fromlist=["x"]).list_proposals(p)]}

    def m_approval_set_pin(self, new: str, old: Optional[str] = None) -> Dict[str, Any]:
        from . import approval
        if approval.agent_environment() and not old and approval.is_set():
            raise ResearchError("Changing the PIN needs the current PIN")
        approval.set_pin(new, old)
        return approval.status()

    def m_attempt_detail(self, task: str, attempt: int = 0) -> Dict[str, Any]:
        """One attempt: its exact prompt, context sources, verification, reports and log tails."""
        from . import execution as E, planspec as PS, supervisor as SV
        p = self.project(sync=False)
        hit = PS.find_task_key(p, task)
        rs = hit and SV.load_run(p, hit[0]["plan_id"])
        atts = ((rs or {}).get("tasks", {}).get(task) or {}).get("attempts") or []
        if not atts:
            return {"task": task, "attempts": []}
        a = atts[attempt - 1] if attempt else atts[-1]
        prompt = None
        if a.get("prompt_path"):
            try:
                prompt = open(p.abspath(a["prompt_path"]), encoding="utf-8").read()
            except OSError:
                prompt = None
        logs = None
        if a.get("execution"):
            try:
                logs = E.logs(p, a["execution"], 6000)
            except (OSError, ResearchError):
                logs = None
        return {"task": task, "attempt": a, "attempts": len(atts), "prompt": prompt, "logs": logs, "reports": SV._reports(p, task, a.get("execution"))[-40:]}

    def m_plan_promote(self, plan: str, experiment: Optional[str] = None) -> Dict[str, Any]:
        from . import supervisor as SV
        return SV.promote_plan(self.project(), plan, experiment)

    def m_promotion_hint(self, plan: str) -> Dict[str, Any]:
        from . import planspec as PS, workspace as W
        p = self.project(sync=False)
        st = PS._state_for(p, plan)
        out = []
        for ek, eid in (st.get("experiments") or {}).items():
            try:
                for ws in W.list_workspaces(p):
                    if ws.get("experiment") == eid:
                        out.append({"experiment": eid, "workspace": ws["id"], "path": ws.get("path"), "branch": ws.get("branch"), "ahead": ws.get("commits_ahead"),
                                    "uncommitted": ws.get("dirty") or ws.get("untracked")})
            except ResearchError:
                continue
        return {"plan": plan, "workspaces": out}

    # agents ------------------------------------------------------------------------------------
    def m_agents(self) -> Dict[str, Any]:
        from . import agents as AG
        return AG.list_profiles(self.project(sync=False))

    def m_dispatch(self, role: Optional[str] = None, target: Optional[str] = None, objective: Optional[str] = None,
                   profile: Optional[str] = None, model: Optional[str] = None,
                   instructions: Optional[str] = None, headless: bool = False, effort: Optional[str] = None,
                   strategy: Optional[str] = None, level: Optional[str] = None,
                   create_workspace: bool = False, allow_unfrozen: Optional[str] = None) -> Dict[str, Any]:
        """PREPARE only: resolves everything and writes the brief. Nothing runs until `execution_start` / the wrapper is run."""
        from . import agents as AG
        d = AG.dispatch(self.project(), role, target, objective, profile, model, instructions, headless=headless, effort=effort,
                        strategy=strategy, level=level, create_workspace=create_workspace, allow_unfrozen=allow_unfrozen)
        d["shell"] = None      # never ship a copy-pasteable string built from task text
        return d

    def m_freeze_tests(self, task: str, paths: Any) -> Dict[str, Any]:
        from . import orchestrate as O
        return O.freeze(self.project(), task, paths)

    def m_execution_start(self, id: str) -> Dict[str, Any]:
        """Start a prepared headless execution detached from the UI. Returns at once; poll `execution_show` for `running`."""
        import subprocess
        from . import execution as EX
        r = EX.show(self.project(sync=False), id)
        if r["state"] != "prepared" or r.get("mode") != "headless":
            raise ResearchError(f"Only a prepared headless execution can be started here (this one is {r['state']}/{r.get('mode')})")
        subprocess.Popen([sys.executable, os.path.abspath(EX.__file__), self.root, id], cwd=r.get("cwd") or self.root,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"id": id, "state": "starting"}

    def m_executions(self, task: Optional[str] = None) -> List[Dict[str, Any]]:
        from . import execution as EX
        return EX.list_all(self.project(sync=False), task)

    def m_execution_show(self, id: str) -> Dict[str, Any]:
        from . import execution as EX
        return EX.show(self.project(sync=False), id)

    def m_execution_logs(self, id: str) -> Dict[str, Any]:
        from . import execution as EX
        return EX.logs(self.project(sync=False), id)

    def m_execution_stop(self, id: str) -> Dict[str, Any]:
        from . import execution as EX
        return EX.stop(self.project(), id)

    def m_execution_retry(self, id: str, profile: Optional[str] = None, model: Optional[str] = None) -> Dict[str, Any]:
        from . import agents as AG
        d = AG.redispatch(self.project(), id, profile, model)
        d["shell"] = None
        return d

    def m_providers(self) -> Dict[str, Any]:
        """Agent providers for the Control Center: readiness, models, MCP, default, strategy, recent executions."""
        from . import execution as EX, providers as PR, strategy as S
        p = self.project(sync=False)
        p.config = p.load_config()
        profs = (p.config.get("agents") or {}).get("profiles") or {}
        rows = []
        for name, prof in profs.items():
            if name in ("claude", "codex", "agy") or prof.get("provider"):
                r = PR.readiness(prof, name, p.root)
                r["models"] = PR.available_models(r["provider"], r["command"], profs)
                r["profiles"] = sorted(n for n, x in profs.items() if PR.provider_of(x, n) == r["provider"] and n != name)
                rows.append(r)
        return {"default": (p.config.get("agents") or {}).get("default_profile"), "strategy": S.active_name(p),
                "providers": rows, "recent": [{**{k: e.get(k) for k in ("id", "task", "state", "mode", "exit_code", "prepared_at", "cwd")},
                    "profile": (e.get("request") or {}).get("profile"), "model": (e.get("request") or {}).get("model")} for e in EX.list_all(p)[:15]],
                "instructions": PR.instruction_files(p.root)}

    def m_provider_default(self, name: str) -> Dict[str, Any]:
        """Change the project default agent (same setting as the YAML key agents.default_profile). Future dispatches only."""
        from . import registry
        return registry.apply(self.project(), {"agents.default_profile": name}, reason="provider default")

    def m_provider_mcp(self, name: str, apply: bool = False) -> Dict[str, Any]:
        from . import providers as PR, workspace as W
        p = self.project(sync=False)
        prof = ((p.config.get("agents") or {}).get("profiles") or {}).get(name) or {}
        kind = PR.provider_of(prof, name)
        done = PR.mcp_install(kind, p.root) if apply else None
        ws = W.list_workspaces(p)
        return {"installed": done, "verify": PR.mcp_verify(p.root, ws[0]["path"] if ws else p.root)}

    def m_init_environment(self) -> Dict[str, Any]:
        from . import initproject
        return initproject.environment(self.root)

    def m_init_preview(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        from . import initproject
        return initproject.plan(self.root, spec)

    def m_init_yaml_template(self) -> Dict[str, Any]:
        from . import bootstrap
        return {"text": bootstrap.TEMPLATE}

    def m_init_yaml_check(self, text: str) -> Dict[str, Any]:
        from . import bootstrap
        return bootstrap.check(text, self.root)

    def m_init_yaml_apply(self, text: str) -> Dict[str, Any]:
        from . import bootstrap
        return bootstrap.apply(text, self.root, author=self.author)

    def m_init_yaml_export(self) -> Dict[str, Any]:
        from . import bootstrap
        return {"text": bootstrap.export(self.project(sync=False))}

    def m_init_apply(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        from . import initproject
        return initproject.apply(self.root, spec, author=self.author)

    def m_hpc_discover(self) -> Dict[str, Any]:
        """SSH aliases from ~/.ssh/config. Reads a local file; contacts nothing."""
        from . import initproject
        return {"hosts": initproject.ssh_hosts()}

    def m_env_probe(self, python: str, packages: Optional[List[str]] = None) -> Dict[str, Any]:
        """Test an existing interpreter or venv before any project exists (the wizard's *Use an existing environment*)."""
        from types import SimpleNamespace
        from . import envcheck
        fake = SimpleNamespace(config={"environment": {"python": python, "packages": packages or []}}, root=self.root, rdir=self.root)
        return envcheck.check(fake, packages)

    def m_hpc_test(self, ssh_host: str, workspace_root: str = "", python: str = "", required_packages: Any = None, setup: Any = None,
                   partition: str = "", compute: bool = False) -> Dict[str, Any]:
        """Explicit, read-only connection test (the existing `hpc doctor` checks) before any project exists. With `compute`, also one
        one-minute srun to prove the job environment on a compute node."""
        from types import SimpleNamespace
        from .hpc import remote as HR
        if not ssh_host or any(c.isspace() for c in ssh_host) or ssh_host.startswith("-"):
            raise ResearchError("Invalid SSH host alias")
        lines = lambda v: [x.strip() for x in (v.splitlines() if isinstance(v, str) else (v or [])) if str(x).strip()]
        cl: Dict[str, Any] = {"ssh_host": ssh_host, "paths": {"workspace_root": workspace_root or "~/research-os-probe"}}
        if python:
            cl["python"] = python
        if required_packages:
            cl["required_packages"] = [x.strip() for x in (required_packages.split(",") if isinstance(required_packages, str) else required_packages) if str(x).strip()]
        sl = {k: v for k, v in (("setup", lines(setup)), ("partition", partition or None)) if v}
        if sl:
            cl["slurm"] = sl
        fake = SimpleNamespace(config={"hpc": {"clusters": {"probe": cl}}}, name="probe", root=self.root)
        return HR.doctor(fake, "probe", compute=compute)

    def m_mcp_install(self, clients: Any = None) -> Dict[str, Any]:
        from . import mcp as MCP
        return MCP.install(self.project(sync=False).root, clients)

    # agent setup: strategy, harness, skills, MCP, orchestration (one read for the UI) -----------------
    def m_agent_setup(self) -> Dict[str, Any]:
        from . import agents as AG
        from . import harness as H
        from . import hooks as HK
        from . import mcp as MCP
        from . import usage as U
        from . import orchestrate as O
        from . import setup_agents as SA
        from . import strategy as S
        p = self.project(sync=False)
        p.config = p.load_config()
        cur = S.active_name(p)
        files = [{k: v for k, v in f.items() if k != "_text"} for f in SA.plan_agent_files(p)]
        hr = H.resolve(p)
        return {"strategy": S.show(p) if cur else None, "strategies": [
                    {**s, "ok": S.validate(p, s["name"])["ok"], "routes": S.definition(p, s["name"])["route"]} for s in S.listing(p)],
                "harness": hr, "harness_warnings": H.warnings(p),
                "skills": [{**{k: s.get(k) for k in ("name", "dir", "description", "path", "source", "always", "applies_to", "level")},
                            "in_harness": s["name"] in hr["skills"]} for s in SK.list_skills(p)],
                "mcp": MCP.status(p.root),
                "orchestration": {"enabled": bool(O.cfg(p).get("enabled")), "limits": O.limits(p), "tiers": O.cfg(p).get("tiers"),
                                  "available": O.cfg(p).get("available"), "agent_files": files},
                "hooks": HK.listing(p), "usage": U.summary(p, "strategy"), "tasks": self._task_rows(p, cur),
                "experiments": self._experiment_rows(p),
                "profiles": [{"name": x["name"], "model": x["model"], "available": x["available"]} for x in AG.list_profiles(p)["profiles"]]}

    def _task_rows(self, p: Project, strat: Optional[str]) -> List[Dict[str, Any]]:
        from . import orchestrate as O
        from . import strategy as S
        rows = []
        for t in p.q("SELECT * FROM tasks WHERE status IN ('todo','running','verify','blocked') ORDER BY id LIMIT 40"):
            t = p.get("task", t["id"])
            led = O.ledger(p, t["id"])
            r = None
            if strat:
                try:
                    x = S.route_task(p, t, led)
                    r = {"level": x["level"], "model": x["model"], "why": x["why"]}
                except ResearchError:
                    pass
            rows.append({"id": t["id"], "title": t["title"], "status": t["status"], "override": led.get("override"), "route": r,
                         "contract": led.get("contract") or {}, "checks": len(t.get("checks") or []),
                         "frozen": sorted(led.get("frozen") or {}), "attempts": len(led.get("attempts") or [])})
        return rows

    def _experiment_rows(self, p: Project) -> List[Dict[str, Any]]:
        out = []
        for e in p.q("SELECT id, title, status FROM experiments WHERE status != 'abandoned' ORDER BY id DESC LIMIT 15"):
            ev = p.q1("SELECT ts, summary FROM events WHERE entity_id=? AND action='validated' ORDER BY id DESC LIMIT 1", (e["id"],))
            out.append({**e, "state": S.experiment_state(p, e["id"]), "validation": ev})
        return out

    def m_tools(self) -> Dict[str, Any]:
        """What the agents get from Research OS besides records: the skills catalog and packs (with install, enable and update state), the
        links into the agent CLIs, hooks (installed and recommended for this project) and the MCP command. Read-only."""
        from . import hooks as HK
        from . import packs as PK
        from . import skills as SK
        p = self.project(sync=False)
        p.config = p.load_config()
        st = PK.state(p)
        installed = [s for s in SK.list_skills(p) if s.get("enabled", True)]
        links = {}
        for key, rel in SK.LINK_TARGETS.items():
            d = os.path.join(p.root, rel)
            links[key] = {"dir": rel, "linked": sorted(n["name"] for n in installed if os.path.exists(os.path.join(d, n["dir"] or n["name"])))}
        rec = HK.recommended(p)
        return {"catalog": st["catalog"], "packs": st["packs"], "own": st["own"], "enabled_packs": st["enabled_packs"], "disabled": st["disabled"],
                "skills": [{"name": s["name"], "dir": s["dir"], "description": s.get("description"), "level": s["level"], "always": s["always"],
                            "applies_to": s["applies_to"], "source": s.get("source"), "enabled": s.get("enabled", True)} for s in SK.list_skills(p)],
                "enabled_skill_count": len(installed), "links": links, "link_targets": list((p.config.get("skills") or {}).get("link") or []),
                "hooks": HK.listing(p), "recommended_hooks": rec["hooks"], "stacks": rec["stacks"],
                "starters": {n: {"stage": s["stage"], "command": s["command"], "description": s["description"], "installed": n in HK.specs(p)}
                             for n, s in HK.STARTERS.items()},
                "config_files": {"project": p.rel(p.config_path), "machine": __import__("research.configfile", fromlist=["x"]).machine_path()},
                "mcp_command": __import__("research.providers", fromlist=["x"])._server_cmd(self.root)}

    def m_pack_set(self, id: str, enabled: bool = True, optional: bool = False) -> Dict[str, Any]:
        from . import packs as PK
        p = self.project(sync=False)
        return PK.enable_pack(p, id, optional) if enabled else PK.disable_pack(p, id)

    def m_skill_install(self, names: Any, force: bool = False) -> Dict[str, Any]:
        from . import packs as PK
        from . import skills as SK
        p = self.project(sync=False)
        out = [PK.install_skill(p, n, None, force) for n in (names if isinstance(names, list) else [names])]
        SK.link_skills(p)
        return {"results": out}

    def m_skill_toggle(self, name: str, enabled: bool) -> Dict[str, Any]:
        from . import packs as PK
        return PK.set_enabled(self.project(sync=False), name, enabled)

    def m_skills_upgrade(self) -> Dict[str, Any]:
        from . import packs as PK
        return PK.update(self.project(sync=False))

    def m_hook_install(self, names: Any = None, approve: bool = False) -> Dict[str, Any]:
        from . import hooks as HK
        return HK.install(self.project(sync=False), names, approve)

    def m_hook_install_starters(self, approve: bool = True) -> Dict[str, Any]:
        from . import hooks as HK
        return HK.install_starters(self.project(sync=False), approve)

    def m_hook_approve(self, names: Any = None) -> Dict[str, Any]:
        from . import hooks as HK
        return HK.approve(self.project(sync=False), names)

    def m_hook_toggle(self, name: str, enabled: bool) -> Dict[str, Any]:
        from . import hooks as HK
        return HK.set_enabled(self.project(sync=False), name, enabled)

    def m_hook_run(self, name: str) -> Dict[str, Any]:
        from . import hooks as HK
        p = self.project(sync=False)
        return HK.run(p, HK.specs(p)[name]["stage"], names=[name])

    def m_task_pin(self, id: str, level: Optional[str] = None, profile: Optional[str] = None, clear: bool = False) -> Dict[str, Any]:
        from . import orchestrate as O
        return O.set_override(self.project(sync=False), id, level, profile, None, clear)

    def m_experiment_check(self, id: str, request_synthesis: bool = False) -> Dict[str, Any]:
        from . import expcheck
        return expcheck.check(self.project(), id, request_synthesis)

    def m_strategy_use(self, name: str, dry_run: bool = False) -> Dict[str, Any]:
        from . import strategy as S
        p = self.project(sync=False)
        p.config = p.load_config()
        v, prev = S.validate(p, name), S.active_name(p)
        if not v["ok"]:
            raise ResearchError(f"Strategy {name} is invalid: {v['errors'][0]}", hint="Fix it in .research/config.yaml.")
        if not dry_run and prev != name:
            p.save_config({"orchestration.active_strategy": name})
            p.event("project", None, "strategy_changed",
                    f"allocation strategy {prev or 'legacy'} → {name}@{S.fingerprint(p, name)} (applies to future dispatches only)")
        return {"previous": prev, "active": name, "dry_run": dry_run, "warnings": v["warnings"]}

    def m_setup_agents(self, apply: bool = False, strategy: Optional[str] = None) -> Dict[str, Any]:
        from . import setup_agents as SA
        return SA.setup(self.project(sync=False), apply=apply, strategy=strategy)

    # skills ------------------------------------------------------------------------------------
    def m_skills_add(self, source: str, only: Any = None, force: bool = False, always: Optional[bool] = None,
                     applies_to: Any = None, list_only: bool = False) -> Dict[str, Any]:
        return SK.add_skills(self.project(), source, only, force, always, applies_to, list_only=list_only)

    def m_skills_remove(self, name: str) -> Dict[str, Any]:
        return SK.remove_skill(self.project(), name)

    def m_skills_update(self, names: Any = None) -> Dict[str, Any]:
        return SK.update_skills(self.project(), names)

    def m_skills_set(self, name: str, always: Optional[bool] = None, applies_to: Any = None,
                     level: Optional[str] = None, checks: Any = None) -> Dict[str, Any]:
        return SK.set_skill(self.project(), name, always, applies_to, level, checks)

    def m_skills_link(self) -> Dict[str, Any]:
        return SK.link_skills(self.project(sync=False))



MUTATING = {
    "pack_set", "skill_install", "skill_toggle", "skills_upgrade", "hook_install", "hook_install_starters", "skills_link", "hook_approve",
    "hook_toggle", "hook_run", "guide_init", "guide_build", "guide_highlight", "guide_log", "guide_draft", "guide_draft_done",
    "guide_cite_add", "plan_promote", "plan_yaml_apply", "plan_control", "supervisor_task", "proposal_decide", "approval_set_pin",
    "init_yaml_apply", "supervisor_daemon", "intervention_request", "intervention_action", "debug_run", "telegram_set", "telegram_test",
    "telegram_clear", "hpc_submit", "hpc_init", "hpc_sync", "freeze_tests", "create", "update", "set_status", "set_baseline", "synthesize",
    "supersede_finding", "run_attach", "run_start", "run_cancel", "register_artifact", "log_metric", "init", "update_project", "sync",
    "rebuild", "release_task", "task_note", "verify_task", "review_finding", "report", "dispatch", "skills_add", "skills_remove",
    "skills_update", "skills_set", "strategy_use", "setup_agents", "task_pin", "experiment_check"}


def serve(root: str, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    srv = Server(root)

    def send(obj: Dict[str, Any]) -> None:
        stdout.write(json.dumps(obj, default=str, ensure_ascii=False) + "\n")
        stdout.flush()

    send({"event": "ready", "version": __version__, "protocol": PROTOCOL, "root": srv.root, "initialized": srv.initialized()})
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send({"id": None, "error": {"message": "invalid JSON", "code": -32700}})
            continue
        if not isinstance(req, dict) or not isinstance(req.get("method"), str):
            send({"id": None, "error": {"message": "invalid request", "code": -32600}})
            continue
        rid, method, params = req.get("id"), req["method"], req.get("params", {})
        if not isinstance(params, dict):
            send({"id": rid, "error": {"message": "params must be an object", "code": -32602}})
            continue
        fn = getattr(srv, "m_" + method, None)
        if fn is None:
            send({"id": rid, "error": {"message": f"unknown method {method}", "code": -32601}})
            continue
        try:
            if "author" in params:  # optional author override from the UI
                author = params.pop("author")
                if not isinstance(author, dict):
                    send({"id": rid, "error": {"message": "bad parameters for " + method + ": author must be an object", "code": -32602}})
                    continue
                srv.author = detect_author(**{k: v for k, v in author.items()
                                              if k in ("agent", "model", "author_type")})
                if srv.p:
                    srv.p.author = srv.author
            if method not in ("ping", "init", "doctor", "init_environment", "init_preview", "init_apply", "init_yaml_template", "init_yaml_check", "init_yaml_apply", "hpc_discover", "hpc_test") and not srv.initialized():
                raise NotInitialized("Project not initialized", hint="Run 'Research: Initialize Project'.")
            try:
                inspect.signature(fn).bind(**params)
            except TypeError as e:  # only a bad call shape is -32602; a TypeError inside the method is a real error
                send({"id": rid, "error": {"message": f"bad parameters for {method}: {e}", "code": -32602}})
                continue
            result = fn(**params)
            if method in MUTATING and srv.p is not None:
                try:
                    C.write_current_md(srv.p)
                except Exception:
                    pass
            send({"id": rid, "result": result})
        except ResearchError as e:
            send({"id": rid, "error": {"message": str(e), "hint": e.hint, "code": e.exit_code, "data": e.data,
                                       "kind": type(e).__name__}})
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
