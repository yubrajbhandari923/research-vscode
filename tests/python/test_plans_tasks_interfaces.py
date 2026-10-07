"""Plans/tasks through the CLI, RPC (exact VS Code form payloads), current.md and Research Home."""
import io
import json
import re
import shlex

import pytest

from research import context as C
from research import plans as P
from research import services as S
from research import views as V
from research.store import TEMPLATES
from research_cli.main import build_parser, main


def rpc(repo, *reqs):
    """Run requests through the real RPC server; return {id: response}."""
    from research.rpc import serve
    out = io.StringIO()
    msgs = [{"id": i + 1, "method": m, "params": p} for i, (m, p) in enumerate(reqs)]
    serve(str(repo), io.StringIO("\n".join(json.dumps(r) for r in msgs) + "\n"), out)
    lines = [json.loads(l) for l in out.getvalue().splitlines()][1:]
    return {l["id"]: l for l in lines}


def _plan_with_tasks(p):
    q = S.create_question(p, "Does it reproduce?")
    e = S.create_experiment(p, "repro run", question=q["id"])
    plan = P.create_plan(p, "Reproduce Fig. 3", objective="Match the curve", root_question_id=q["id"])
    t1 = P.create_task(p, plan["id"], "Implement baseline", task_type="implementation")
    t2 = P.create_task(p, plan["id"], "Sanity run", depends_on=[t1["id"]], related_experiment_id=e["id"],
                       related_question_id=q["id"], goal="Smallest config runs end to end")
    return q, e, plan, t1, t2


# ---------------------------------------------------------------------------- bug 1: UI plan/task forms via RPC

def test_rpc_create_plan_with_ui_form_payload(proj, repo):
    S.create_question(proj, "Q")
    form = {"title": "From the form", "objective": "obj", "success_criteria": None, "context": None}
    r = rpc(repo,
            ("create", {"type": "plan", **form, "root_question_id": None, "status": "active"}),
            ("create", {"type": "plan", **form, "root_question_id": "Q-001", "status": "active"}),
            # older payload shape (field named `question`) must keep working too
            ("create", {"type": "plan", **form, "question": "q-1", "status": "active"}))
    assert all("result" in r[i] for i in (1, 2, 3)), r
    assert r[1]["result"]["root_question_id"] is None
    assert r[2]["result"]["root_question_id"] == "Q-001"
    assert r[3]["result"]["root_question_id"] == "Q-001"


def test_rpc_task_create_and_edit_with_ui_form_payloads(proj, repo):
    q, e, plan, t1, _ = _plan_with_tasks(proj)
    create = {"type": "task", "plan_id": plan["id"], "title": "From the form", "goal": None, "task_type": "research",
              "assigned_role": None, "depends_on": [t1["id"]], "inputs": None, "expected_outputs": None,
              "related_question_id": q["id"], "related_experiment_id": None, "acceptance_criteria": None,
              "verification": None, "status": "todo"}
    # edit form: blank() turns null → '' and taskValues() turns unset type/role/links back into null
    edit = {"type": "task", "id": "T-003", "title": "Edited", "goal": "", "task_type": None, "assigned_role": None,
            "status": "verify", "depends_on": [], "inputs": "", "expected_outputs": "", "related_question_id": None,
            "related_experiment_id": None, "acceptance_criteria": "tests pass", "verification": "", "result": "",
            "blockers": "", "notes": ""}
    plan_edit = {"type": "plan", "id": plan["id"], "title": "Renamed", "objective": "", "success_criteria": "",
                 "status": "active", "root_question_id": "", "context": ""}
    r = rpc(repo, ("create", create), ("update", edit), ("update", plan_edit), ("show", {"id": "T-003"}))
    assert all("result" in r[i] for i in r), r
    assert r[1]["result"]["depends_on"] == ["T-001"]
    t = r[4]["result"]
    assert t["title"] == "Edited" and t["status"] == "verify" and t["acceptance_criteria"] == "tests pass"
    assert t["depends_on"] is None and t["task_type"] is None and t["related_question_id"] is None
    assert r[3]["result"]["title"] == "Renamed" and r[3]["result"]["root_question_id"] is None


def test_rpc_plan_task_methods(proj, repo):
    _plan_with_tasks(proj)
    r = rpc(repo, ("next_task", {"plan_id": "PLAN-001"}), ("start_task", {"id": "T-001"}),
            ("complete_task", {"id": "T-001", "result": "ok"}), ("task_context", {"id": "T-002"}),
            ("block_task", {"id": "T-002", "blockers": "no data"}), ("home", {}), ("tree", {}),
            ("set_status", {"id": "PLAN-001", "status": "blocked"}))
    assert all("result" in r[i] for i in r), r
    assert r[1]["result"]["id"] == "T-001"
    assert r[3]["result"]["status"] == "done" and r[3]["result"]["completed_at"]
    assert r[4]["result"]["is_ready"] is True
    home = r[6]["result"]
    assert home["active_plan"]["id"] == "PLAN-001"
    assert [a["kind"] for a in home["attention_items"]][:1] == ["task_blocked"]
    assert home["current_focus"]["source"] == "plan" and home["current_focus"]["blocker"] == "no data"
    assert r[7]["result"]["plans"][0]["tasks"][1]["status"] == "blocked"
    assert r[8]["result"]["status"] == "blocked"


# ---------------------------------------------------------------------------- bug 2: commands named in AGENTS.md exist

def _agents_commands():
    text = (TEMPLATES / "AGENTS.block.md").read_text(encoding="utf-8")
    cmds = re.findall(r"`(research [^`]+)`", text)
    cmds += [l.strip() for l in text.splitlines() if l.strip().startswith("research ")]
    return sorted(set(cmds))


def test_agents_md_only_references_real_commands():
    ap = build_parser()
    cmds = _agents_commands()
    assert len(cmds) >= 6
    for c in cmds:
        argv = shlex.split(c.split("#")[0])[1:]
        argv = [a if a not in ("<name>", "<ID>") else "x" for a in argv]
        if "--" in argv:
            argv = argv[:argv.index("--")]
        try:
            ap.parse_args(argv)
        except SystemExit as e:
            if e.code != 0:  # `--help` exits 0
                pytest.fail(f"AGENTS.md references an invalid command: {c}")


def test_cli_current_task_context_and_skill_show(proj, repo, capsys):
    _plan_with_tasks(proj)
    assert main(["current"]) == 0
    out = capsys.readouterr().out
    assert "# Research context" in out and "## Active plans" in out and "next ready: T-001" in out

    assert main(["task", "context", "T-002"]) == 0
    out = capsys.readouterr().out
    assert "Plan PLAN-001" in out and "Match the curve" in out and "Dependencies" in out
    assert "Experiment EXP-001" in out and "Question Q-001" in out and "research task done T-002" in out

    assert main(["task", "context", "2", "--json"]) == 0
    c = json.loads(capsys.readouterr().out)
    assert c["task"]["id"] == "T-002" and c["is_ready"] is False and c["experiment"]["state"]["runs"] == 0

    # blockers show only while blocked (kept on the task as history)
    P.block_task(proj, "T-002", "no data")
    assert P.task_context(proj, "T-002")["task"]["blockers"] == "no data"
    P.update_task(proj, "T-002", status="todo")
    assert P.task_context(proj, "T-002")["task"]["blockers"] is None
    assert proj.get("task", "T-002")["blockers"] == "no data"

    assert main(["skill", "show", "run-experiment"]) == 0
    assert "Run an experiment" in capsys.readouterr().out
    assert main(["skills", "show", "nope"]) == 4
    assert "Available:" in capsys.readouterr().err


# ---------------------------------------------------------------------------- bug 3: `research mark` on plans/tasks

def test_mark_plan_and_task(proj, repo, capsys):
    _plan_with_tasks(proj)
    assert main(["mark", "PLAN-001", "completed"]) == 0
    assert main(["mark", "T-2", "blocked", "--reason", "missing calibration data"]) == 0
    assert main(["mark", "T-001", "nonsense"]) == 1
    capsys.readouterr()
    p = proj.__class__(str(repo))
    try:
        assert p.get("plan", "PLAN-001")["status"] == "completed"
        assert p.get("plan", "PLAN-001")["completed_at"]
        t = p.get("task", "T-002")
        assert t["status"] == "blocked" and t["blockers"] == "missing calibration data"
    finally:
        p.close()


# ---------------------------------------------------------------------------- bug 4: current.md lists plans

def test_current_md_includes_active_plan_queue(proj):
    _plan_with_tasks(proj)
    P.start_task(proj, "T-001")
    md = C.render_current_md(proj)
    assert "## Active plans" in md
    assert "PLAN-001 Reproduce Fig. 3 — 0/2 tasks done · objective: Match the curve" in md
    assert "running: T-001 Implement baseline" in md
    P.complete_task(proj, "T-001")
    P.block_task(proj, "T-002", "no data")
    md = C.render_current_md(proj)
    assert "BLOCKED: T-002 Sanity run — no data" in md
    P.update_plan(proj, "PLAN-001", status="completed")
    assert "## Active plans\n\n_none_" in C.render_current_md(proj)


# ---------------------------------------------------------------------------- bug 5: plan activity

def test_plan_detail_has_events_for_plan_and_tasks(proj):
    _plan_with_tasks(proj)
    P.start_task(proj, "T-001")
    d = V.show(proj, "PLAN-001")
    ids = {e["entity_id"] for e in d["events"]}
    assert {"PLAN-001", "T-001", "T-002"} <= ids


# ---------------------------------------------------------------------------- bug 9: list fields render as []

def test_task_mirror_lists_render_as_empty_lists(proj):
    _plan_with_tasks(proj)
    text = open(proj.mirror_path("task", "T-001"), encoding="utf-8").read()
    assert "depends_on: []" in text and "artifacts: []" in text and "{}" not in text.split("---")[1]
    # legacy files written with {} still import cleanly and are normalised on the next export
    path = proj.mirror_path("task", "T-001")
    open(path, "w", encoding="utf-8").write(text.replace("depends_on: []", "depends_on: {}").replace("title: Implement baseline", "title: Edited"))
    proj.sync()
    assert proj.get("task", "T-001")["title"] == "Edited"
    assert P.get_next_ready_task(proj, "PLAN-001")["id"] == "T-001"
    assert "depends_on: []" in open(path, encoding="utf-8").read()


# ---------------------------------------------------------------------------- bug 10: label/value spacing

def test_cli_long_labels_keep_a_space(proj, repo, capsys):
    P.create_plan(proj, "P", success_criteria="RMSE < 0.05")
    P.create_task(proj, "PLAN-001", "t", expected_outputs="a plot")
    main(["plan", "show", "PLAN-001"])
    main(["task", "show", "T-001"])
    out = capsys.readouterr().out
    assert "success criteria RMSE < 0.05" in out and "expected outputs a plot" in out
