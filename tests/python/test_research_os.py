"""Research OS features: coordination, verification gates, skills, search, context budget, compare, sweep, report,
git snapshot, agent dispatch, MCP server."""
import io
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from research import agents as AG
from research import compare as CMP
from research import context as C
from research import mcp as MCP
from research import plans as P
from research import report as RP
from research import runs as R
from research import schema
from research import services as S
from research import skills as SK
from research import verification as V
from research.search import search
from research.store import Project
from research.util import BudgetExceeded, PolicyBlocked, ResearchError
from research_cli.main import main

from conftest import git


def as_agent(p, name="claude", role=None, monkeypatch=None):
    """Re-open the project as an agent identity (what RESEARCH_AGENT / RESEARCH_AGENT_ROLE do)."""
    from research.util import detect_author
    if monkeypatch is not None:
        if role:
            monkeypatch.setenv("RESEARCH_AGENT_ROLE", role)
        else:
            monkeypatch.delenv("RESEARCH_AGENT_ROLE", raising=False)
    p.author = detect_author(agent=name)
    return p


def as_human(p):
    from research.util import detect_author
    p.author = detect_author(author_type="human")
    return p


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


# ============================================================================ schema v3

def test_migration_v2_to_v3_adds_columns_and_reviews(tmp_path):
    db = str(tmp_path / "old.db")
    conn = schema.connect(db)
    for stmt in schema.MIGRATIONS[1] + schema.MIGRATIONS[2]:
        stmt(conn) if callable(stmt) else conn.execute(stmt)
    for col in ("skills", "checks", "claimed_by", "claimed_at"):
        conn.execute(f"ALTER TABLE tasks DROP COLUMN {col}")
    conn.execute("ALTER TABLE plans DROP COLUMN skills")
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version','2')")
    assert schema.migrate(conn) == 3
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert {"skills", "checks", "claimed_by", "claimed_at"} <= cols
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='reviews'").fetchone()
    assert schema.migrate(conn) == 3  # idempotent


# ============================================================================ coordination

def test_claims_release_and_next(proj, monkeypatch):
    plan = P.create_plan(proj, "P")
    t1 = P.create_task(proj, plan["id"], "one")
    t2 = P.create_task(proj, plan["id"], "two")
    as_agent(proj, "claude", "implementer", monkeypatch)
    started = P.start_task(proj, t1["id"])
    assert started["claimed_by"] == "claude/implementer" and started["claimed_at"]
    assert P.get_next_ready_task(proj, plan["id"])["id"] == t2["id"]  # claimed tasks are skipped
    as_agent(proj, "codex", None, monkeypatch)
    with pytest.raises(PolicyBlocked, match="claimed by claude/implementer"):
        P.start_task(proj, t1["id"])
    assert P.start_task(proj, t1["id"], force=True)["claimed_by"] == "codex"
    rel = P.release_task(proj, t1["id"], "handing back: loader half done")
    assert rel["status"] == "todo" and rel["claimed_by"] is None
    notes = P.progress_notes(proj, t1["id"])
    assert notes[0]["text"] == "handing back: loader half done" and notes[0]["author_name"] == "codex"


def test_start_keeps_role_and_warns_when_not_ready(proj):
    plan = P.create_plan(proj, "P")
    t1 = P.create_task(proj, plan["id"], "one", assigned_role="verifier")
    t2 = P.create_task(proj, plan["id"], "two", depends_on=[t1["id"]])
    assert P.start_task(proj, t1["id"])["assigned_role"] == "verifier"
    assert "not ready" in P.start_task(proj, t2["id"])["warnings"][0]


def test_stale_tasks_reach_home_and_current_md(proj):
    plan = P.create_plan(proj, "P")
    t = P.create_task(proj, plan["id"], "slow")
    P.start_task(proj, t["id"])
    proj.conn.execute("UPDATE tasks SET updated_at='2020-01-01T00:00:00+00:00', claimed_at='2020-01-01T00:00:00+00:00'")
    proj.conn.execute("UPDATE events SET ts='2020-01-01T00:00:00+00:00' WHERE entity_id=?", (t["id"],))
    stale = P.stale_tasks(proj)
    assert [x["id"] for x in stale] == [t["id"]] and stale[0]["idle_hours"] > 1000
    home = C.research_home(proj)
    assert any(a["kind"] == "stale_task" and a["id"] == t["id"] for a in home["attention_items"])
    assert "STALE" in C.render_current_md(proj)
    P.add_task_note(proj, t["id"], "still going")  # activity clears staleness
    assert P.stale_tasks(proj) == []


# ============================================================================ verification: task checks

def test_parse_check_forms():
    assert V.parse_check("cmd:pytest -q") == {"type": "command", "run": "pytest -q"}
    assert V.parse_check("make test") == {"type": "command", "run": "make test"}
    assert V.parse_check("file: out/a.png") == {"type": "file", "path": "out/a.png"}
    assert V.parse_check("metric:EXP-2:rmse<=0.05") == {"type": "metric", "name": "rmse", "op": "<=", "value": 0.05, "target": "EXP-002"}
    m = V.parse_check("metric: acc >= 0.9")
    assert m["op"] == ">=" and m["value"] == 0.9 and "target" not in m
    for c in ("cmd:x", "file:y", "metric:RUN-0001:loss<1"):
        assert V.parse_check(V.describe_check(V.parse_check(c))) == V.parse_check(c)  # round trip
    with pytest.raises(ResearchError):
        V.parse_check("metric:rmse~0.1")


def test_checks_gate_completion_for_agents_warn_humans(proj, repo, monkeypatch):
    q, e, runs = _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "P")
    t = P.create_task(proj, plan["id"], "impl", related_experiment_id=e["id"],
                      checks=["file:out_0.5.png", "metric:err<=0.01", "file:missing.png", f"cmd:{sys.executable} -c 'exit(0)'"])
    as_agent(proj, "claude", "implementer", monkeypatch)
    P.start_task(proj, t["id"])
    with pytest.raises(PolicyBlocked, match="3/4 checks passed: file: missing.png"):
        P.complete_task(proj, t["id"])
    assert proj.get("task", t["id"])["status"] == "running"
    last = V.latest(proj, t["id"], "check")
    assert last["verdict"] == "fail" and len(last["details"]) == 4
    assert any(a["kind"] == "verification_failed" for a in V.attention_items(proj))
    as_human(proj)
    done = P.complete_task(proj, t["id"])  # humans: warning, not a block
    assert done["status"] == "done" and "can't be completed" in done["warnings"][0]


def test_verify_moves_verify_tasks(proj, repo):
    _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "P")
    t = P.create_task(proj, plan["id"], "impl", checks=["file:out_0.5.png"], status="verify")
    res = V.verify_task(proj, t["id"])
    assert res["passed"] and res["task"]["status"] == "done" and res["record"]["id"] == f"{t['id']}/V1"
    t2 = P.create_task(proj, plan["id"], "impl2", checks=["file:nope.png"], status="verify")
    assert V.verify_task(proj, t2["id"])["task"]["status"] == "running"
    with pytest.raises(ResearchError, match="has no checks"):
        V.verify_task(proj, P.create_task(proj, plan["id"], "x")["id"])


# ============================================================================ verification: finding gate

def test_agents_cannot_self_promote_findings(proj, repo, monkeypatch):
    q, e, runs = _exp_with_runs(proj, repo)
    as_agent(proj, "claude", "implementer", monkeypatch)
    with pytest.raises(PolicyBlocked):
        S.create_finding(proj, "alpha 0.5 best", supports=[runs[1]["id"]], status="supported")
    f = S.create_finding(proj, "alpha 0.5 best", supports=[runs[1]["id"]])
    for attempt in (lambda: S.set_status(proj, f["id"], "supported"),
                    lambda: S.update_finding(proj, f["id"], status="supported"),
                    lambda: V.review_finding(proj, f["id"], "supported")):
        with pytest.raises(PolicyBlocked):
            attempt()
    assert proj.get("finding", f["id"])["status"] == "preliminary"
    assert any(a["kind"] == "awaiting_review" for a in V.attention_items(proj))

    as_agent(proj, "claude", "verifier", monkeypatch)  # same CLI, different role = independent
    r = V.review_finding(proj, f["id"], "needs_work", "single seed")
    assert r["finding"]["status"] == "preliminary" and r["review"]["author_name"] == "claude/verifier"
    assert any(a["kind"] == "reviewer_disagrees" for a in C.research_home(proj)["attention_items"])
    r = V.review_finding(proj, f["id"], "supported", "plots check out", confidence="medium")
    assert r["finding"]["status"] == "supported" and r["finding"]["confidence"] == "medium"
    assert not any(a["kind"] == "reviewer_disagrees" for a in V.attention_items(proj))

    bare = S.create_finding(proj, "no evidence")
    as_agent(proj, "codex", None, monkeypatch)
    with pytest.raises(PolicyBlocked, match="no supporting evidence"):
        V.review_finding(proj, bare["id"], "supported")
    contra = V.review_finding(proj, bare["id"], "contradicted", "plot shows the opposite")
    assert contra["finding"]["status"] == "contradicted" and "plot shows the opposite" in contra["finding"]["contradicting_evidence"]

    as_human(proj)  # humans may set supported directly
    assert S.set_status(proj, bare["id"], "supported")["status"] == "supported"


def test_reviews_survive_rebuild(proj, repo, monkeypatch):
    q, e, runs = _exp_with_runs(proj, repo)
    f = S.create_finding(proj, "x", supports=[runs[0]["id"]])
    V.review_finding(proj, f["id"], "supported", "ok")
    proj.rebuild_index(backup=True)
    assert V.latest(proj, f["id"], "review")["summary"] == "ok"


# ============================================================================ skills

def _skill_repo(tmp_path):
    root = tmp_path / "ponytail"
    for d, name, desc in (("skills/ponytail", "ponytail", "Replace fifty lines with one."),
                          (".openclaw/skills/ponytail", "ponytail", "hidden copy"),
                          ("skills/nv-segment-ct", "nv-segment-ct", "Segment CT volumes."),
                          ("docs/examples/demo", "demo-skill", "an example elsewhere")):
        (root / d).mkdir(parents=True)
        (root / d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n")
    (root / "skills/nv-segment-ct/scripts").mkdir()
    (root / "skills/nv-segment-ct/scripts/run.py").write_text("print(1)\n")
    return root


def test_skills_import_link_match_remove(proj, repo, tmp_path):
    src = _skill_repo(tmp_path)
    found = SK.add_skills(proj, str(src), list_only=True)["available"]
    assert [x["name"] for x in found] == ["demo-skill", "nv-segment-ct", "ponytail"]
    assert next(x for x in found if x["name"] == "ponytail")["subpath"] == "skills/ponytail"  # not the hidden copy
    r = SK.add_skills(proj, str(src), only=["ponytail", "nv-segment-ct"], always=None)
    assert r["added"] == ["nv-segment-ct", "ponytail"]
    assert (repo / ".research/skills/nv-segment-ct/scripts/run.py").exists()
    for d in (".claude/skills", ".agents/skills"):
        link = repo / d / "ponytail"
        assert link.is_symlink() and (link / "SKILL.md").exists()
    (repo / ".claude/skills/mine").mkdir()  # a user's own skill folder is never touched
    with pytest.raises(ResearchError, match="already exists"):
        SK.add_skills(proj, str(src), only=["ponytail"])
    SK.add_skills(proj, str(src), only=["ponytail"], force=True)
    assert list((repo / ".research/cache/removed-skills").iterdir())

    SK.set_skill(proj, "ponytail", always=True)
    SK.set_skill(proj, "nv-segment-ct", applies_to=["experiment"])
    md = C.render_current_md(proj)
    assert "- ponytail — Replace fifty lines with one." in md
    plan = P.create_plan(proj, "P", skills=["custom-missing"])
    t = P.create_task(proj, plan["id"], "segment", task_type="experiment")
    got = {s["name"]: s for s in P.task_context(proj, t["id"])["skills"]}
    assert got["nv-segment-ct"]["why"] == "applies to experiment"
    assert got["ponytail"]["why"] == "always on"
    assert got["custom-missing"]["missing"] is True

    manifest = SK.load_manifest(proj)
    assert manifest["ponytail"]["subpath"] == "skills/ponytail" and manifest["ponytail"]["always"] is True
    SK.update_skills(proj, ["nv-segment-ct"])
    rm = SK.remove_skill(proj, "nv-segment-ct")
    assert not (repo / ".claude/skills/nv-segment-ct").exists() and (repo / ".claude/skills/mine").is_dir()
    assert "nv-segment-ct" not in SK.load_manifest(proj) and rm["kept_at"].startswith(".research/cache/")


def test_skills_from_git_url(proj, tmp_path):
    src = _skill_repo(tmp_path)
    git(src, "init", "-q", "-b", "main")
    git(src, "add", ".")
    git(src, "commit", "-qm", "skills")
    r = SK.add_skills(proj, f"file://{src}", only=["ponytail"])
    m = SK.load_manifest(proj)["ponytail"]
    assert r["added"] == ["ponytail"] and len(m["ref"]) == 40 and m["source"] == f"file://{src}"
    (src / "skills/ponytail/SKILL.md").write_text("---\nname: ponytail\ndescription: v2\n---\nnew\n")
    git(src, "commit", "-qam", "v2")
    SK.update_skills(proj, ["ponytail"])
    assert SK.get_skill(proj, "ponytail")["description"] == "v2"


# ============================================================================ search, context budget

def test_search_and_current_budget(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    S.create_finding(proj, "Entropy regularisation reduces mixing", statement="Mixing drops 40% with entropy", supports=[runs[0]["id"]])
    S.synthesize(proj, e["id"], interpretation="entropy helps a lot")
    hits = search(proj, "entropy")
    assert hits[0]["id"] == "F-001" and {h["type"] for h in hits} >= {"finding", "synthesis"}
    assert search(proj, "entropy mixing", types=["finding"])[0]["snippet"]
    assert search(proj, "nonexistentword") == []
    for i in range(40):
        S.create_finding(proj, f"finding number {i} " + "x" * 120, supports=[runs[0]["id"]])
    proj.config["context"]["max_chars"] = 4000
    md = C.render_current_md(proj)
    assert len(md) <= 4000 and "more (`research finding list`)" in md and "{TOKENS}" not in md


# ============================================================================ compare, sweep, report

def test_compare_runs(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    c = CMP.compare_runs(proj, runs[0]["id"], runs[1]["id"])
    assert c["changed_parameters"] == ["alpha"]
    err = next(m for m in c["metrics"] if m["name"] == "err")
    assert err["a"] == pytest.approx(0.4) and err["b"] == 0 and err["delta"] == pytest.approx(-0.4)
    assert {x["name"] for x in c["artifacts"]} == {"out_0.1.png", "out_0.5.png"} and c["same_commit"]


def test_sweep_budget_and_runs(proj, repo, monkeypatch):
    q = S.create_question(proj, "q")
    e = S.create_experiment(proj, "grid", question=q["id"], parameters={"a": [1, 2, 3], "fixed": 7}, planned_runs=4)
    (repo / "p.py").write_text("import sys; print(sys.argv[1:])\n")
    dry = R.sweep_run(proj, e["id"], f"{sys.executable} p.py {{a}} {{fixed}}", dry_run=True)
    assert [x["command"].split()[-2:] for x in dry["points"]] == [["1", "7"], ["2", "7"], ["3", "7"]]
    assert dry["points"][0]["label"] == "a=1" and dry["points"][0]["parameters"] == {"fixed": 7, "a": 1}
    as_agent(proj, "claude", None, monkeypatch)
    with pytest.raises(BudgetExceeded, match="planned: 4"):
        R.sweep_run(proj, e["id"], "true", grid={"a": [1, 2, 3, 4, 5]})
    with pytest.raises(BudgetExceeded, match="run budget"):
        proj.config["agent_policy"]["max_runs_without_synthesis"] = 2
        R.sweep_run(proj, e["id"], "true", dry_run=True)
    proj.config["agent_policy"]["max_runs_without_synthesis"] = 5
    res = R.sweep_run(proj, e["id"], f"{sys.executable} p.py {{a}}", tee=False)
    assert [r["status"] for r in res["runs"]] == ["completed"] * 3
    assert [r["label"] for r in res["runs"]] == ["a=1", "a=2", "a=3"]


def test_report_md_and_html(proj, repo, monkeypatch):
    q, e, runs = _exp_with_runs(proj, repo)
    arts = R.list_artifacts(proj, run=runs[1]["id"])
    f = S.create_finding(proj, "alpha 0.5 is best", supports=[runs[1]["id"], arts[0]["id"]], limitations="one seed")
    S.synthesize(proj, e["id"], interpretation="U-shaped", next_experiment="probe 1.0-1.5")
    out = RP.write_report(proj, None, "both")
    md = (repo / ".research/reports/project.md").read_text()
    assert "## What we learned" in md and "alpha 0.5 is best" in md and "| run | status | alpha | err |" in md
    assert "](../../out_0.5.png)" in md and "U-shaped" in md
    html = (repo / ".research/reports/project.html").read_text()
    assert '<img src="../../out_0.5.png"' in html and "<table>" in html
    scoped = RP.write_report(proj, q["id"], "md")
    assert scoped["paths"] == [".research/reports/Q-001.md"]
    custom = repo / "docs" / "r.md"
    RP.write_report(proj, e["id"], "md", str(custom))
    assert "](../out_0.5.png)" in custom.read_text()  # image paths follow the output folder


# ============================================================================ git snapshot

def test_checkpoint_commit_only_research(proj, repo):
    (repo / "staged.txt").write_text("keep me staged\n")
    git(repo, "add", "staged.txt")
    c = C.create_checkpoint(proj, current_problem="none", commit=True)
    assert c["snapshot_commit"]
    files = subprocess.run(["git", "show", "--name-only", "--pretty=", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert files.strip() and all(f.startswith(".research/") for f in files.split())
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert "A  staged.txt" in status and ".research" not in status  # nothing of ours left dirty
    C.write_current_md(proj)  # HEAD changed; regenerating must not dirty current.md
    assert ".research" not in subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert C.create_checkpoint(proj, commit=False)["snapshot_commit"] is None


# ============================================================================ agents / dispatch

def test_dispatch_profiles_roles_and_briefs(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "Repro", objective="match fig 3")
    t = P.create_task(proj, plan["id"], "impl", checks=["file:x.png"], related_experiment_id=e["id"])
    f = S.create_finding(proj, "claim", supports=[runs[0]["id"]])
    proj.config["agents"]["roles"]["verifier"] = "codex"
    proj.config["agents"]["profiles"]["codex"]["model"] = "gpt-5"
    d = AG.dispatch(proj, None, t["id"])
    assert d["role"] == "implementer" and d["profile"] == "claude" and d["argv"][0] == "claude"
    assert d["env"]["RESEARCH_AGENT_ROLE"] == "implementer" and d["brief"] in d["argv"][-1]
    brief = (repo / d["brief"]).read_text()
    assert "IMPLEMENTER" in brief and "Task T-001" in brief and "file: x.png" in brief and "match fig 3" in brief
    v = AG.dispatch(proj, None, f["id"])
    assert v["role"] == "verifier" and v["argv"][:3] == ["codex", "-m", "gpt-5"]
    vb = (repo / v["brief"]).read_text()
    assert "check a claim against its evidence" in vb and "task verify" not in vb
    g = AG.dispatch(proj, "planner", objective="Reproduce Table 2", profile="gemini")
    assert g["argv"][:2] == ["gemini", "-i"] and "Reproduce Table 2" in (repo / g["brief"]).read_text()
    assert AG.dispatch(proj, None, e["id"])["role"] == "analyst"
    with pytest.raises(ResearchError, match="needs an objective"):
        AG.dispatch(proj, "planner")
    with pytest.raises(ResearchError, match="Unknown agent profile"):
        AG.dispatch(proj, "analyst", e["id"], profile="nope")


def test_role_env_changes_author_name(monkeypatch):
    from research.util import detect_author
    monkeypatch.setenv("RESEARCH_AGENT", "claude")
    monkeypatch.setenv("RESEARCH_AGENT_ROLE", "verifier")
    assert detect_author()["author_name"] == "claude/verifier"


# ============================================================================ MCP

def _mcp(repo, *msgs, author=None):
    out = io.StringIO()
    MCP.serve(str(repo), io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out, author=author)
    return [json.loads(l) for l in out.getvalue().splitlines()]


def test_mcp_protocol_and_tools(proj, repo):
    q, e, runs = _exp_with_runs(proj, repo)
    plan = P.create_plan(proj, "P")
    P.create_task(proj, plan["id"], "do it", checks=["file:out_0.5.png"])
    call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
    res = _mcp(repo,
               {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "Claude Code"}}},
               {"jsonrpc": "2.0", "method": "notifications/initialized"},
               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
               call(3, "task_next", {}), call(4, "task_start", {"id": "T-001"}),
               call(5, "task_note", {"id": "T-001", "text": "via mcp"}),
               call(6, "task_done", {"id": "T-001", "result": "ok"}),
               call(7, "finding_create", {"title": "mcp claim", "supports": [runs[0]["id"]]}),
               call(8, "finding_review", {"id": "F-001", "verdict": "supported"}),
               call(9, "runs_compare", {"a": runs[0]["id"], "b": runs[1]["id"]}),
               call(10, "research_current", {}), call(11, "task_start", {"bad": 1}),
               {"jsonrpc": "2.0", "id": 12, "method": "ping"},
               {"jsonrpc": "2.0", "id": 13, "method": "nope"})
    by = {r["id"]: r for r in res}
    assert sorted(by) == list(range(1, 14))  # the notification got no response
    assert by[1]["result"]["protocolVersion"] == "2025-06-18" and by[1]["result"]["capabilities"]["tools"]
    names = {t["name"] for t in by[2]["result"]["tools"]}
    assert {"research_current", "task_context", "task_done", "finding_review", "run_start", "skill_show"} <= names
    assert all(t["inputSchema"]["type"] == "object" for t in by[2]["result"]["tools"])
    text = lambda i: by[i]["result"]["content"][0]["text"]
    assert json.loads(text(3))["id"] == "T-001"
    assert json.loads(text(4))["claimed_by"] == "claude-code"  # authorship from clientInfo
    assert json.loads(text(6))["status"] == "done"
    assert by[8]["result"]["isError"] and "independent reviewer" in text(8)
    assert json.loads(text(9))["changed_parameters"] == ["alpha"]
    assert text(10).startswith("# Research context")
    assert by[11]["result"]["isError"] and "Bad arguments" in text(11)
    assert by[12]["result"] == {} and by[13]["error"]["code"] == -32601


def test_mcp_install_merges_configs(repo, proj):
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    r = MCP.install(str(repo), ["claude", "vscode", "cursor", "gemini", "codex"])
    data = json.loads((repo / ".mcp.json").read_text())
    assert data["mcpServers"]["other"] == {"command": "x"} and data["mcpServers"]["research"]["args"] == ["mcp"]
    assert json.loads((repo / ".vscode/mcp.json").read_text())["servers"]["research"]["type"] == "stdio"
    assert (repo / ".cursor/mcp.json").exists() and (repo / ".gemini/settings.json").exists()
    assert "codex mcp add research" in r["installed"]["codex"]


def test_mcp_server_as_subprocess(proj, repo):
    """The real stdio entry point: `python -m research_cli mcp`."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "research_search", "arguments": {"query": "x"}}}]
    p = subprocess.run([sys.executable, "-m", "research_cli", "mcp"], cwd=repo, capture_output=True, text=True,
                       input="\n".join(json.dumps(m) for m in msgs) + "\n", timeout=60)
    lines = [json.loads(l) for l in p.stdout.splitlines()]
    assert lines[0]["result"]["protocolVersion"] in MCP.PROTOCOL_VERSIONS  # unknown version → one we support
    assert lines[1]["result"]["isError"] is False


# ============================================================================ CLI surface

def test_cli_new_commands(proj, repo, tmp_path, capsys):
    q, e, runs = _exp_with_runs(proj, repo)
    src = _skill_repo(tmp_path)
    assert main(["skills", "add", str(src), "--list"]) == 0
    assert main(["skills", "add", str(src), "--only", "ponytail", "--always"]) == 0
    assert main(["plan", "create", "P", "--skill", "ponytail"]) == 0
    assert main(["task", "create", "PLAN-001", "t", "--check", "file:out_0.5.png", "--check", "metric:EXP-001:err<=0.01",
                 "-e", "EXP-001"]) == 0
    assert main(["task", "start", "T-001"]) == 0
    assert main(["task", "note", "T-001", "half", "done"]) == 0
    assert main(["task", "verify", "T-001"]) == 0
    assert main(["task", "done", "T-001", "-r", "ok"]) == 0
    assert main(["finding", "create", "c", "--supports", runs[0]["id"]]) == 0
    assert main(["--agent", "codex", "finding", "review", "F-001", "--verdict", "supported", "--notes", "fine"]) == 0
    assert main(["search", "alpha"]) == 0
    assert main(["run", "compare", runs[0]["id"], runs[1]["id"]]) == 0
    assert main(["run", "sweep", "EXP-001", "--dry-run", "--", "python", "sim.py", "{alpha}"]) == 0
    assert main(["report", "--format", "md"]) == 0
    assert main(["dispatch", "verifier", "F-001", "--print"]) == 0
    assert main(["task", "dispatch", "T-001", "--print", "--profile", "codex"]) == 0
    assert main(["agents"]) == 0
    assert main(["mcp", "install", "--client", "claude"]) == 0
    assert main(["checkpoint", "--commit"]) == 0
    out = capsys.readouterr().out
    for s in ("ponytail", "passed", "T-001", "Δ", "alpha=0.1", "report:", "RESEARCH_AGENT_ROLE=verifier",
              "codex", "claude: .mcp.json", "committed"):
        assert s in out, s
    assert (repo / ".research/reports/project.md").exists()
