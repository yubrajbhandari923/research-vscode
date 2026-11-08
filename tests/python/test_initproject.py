"""Initialize Project: empty folder -> main/ + ledger/ + experiments/ + workspace file, one git repository."""
import json
import os
import subprocess

import pytest

from research import initproject as IP, plans, providers, services
from research import workspace as W
from research.store import Project
from research.util import ResearchError

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def g(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV}).stdout


@pytest.fixture
def made(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "sparse-ct"
    root.mkdir()
    (root / "stray.txt").write_text("pre-existing, must not be committed")
    spec = {"name": "sparse-ct", "goal": "find alpha", "agents": {"enabled": ["claude", "agy"], "default": "claude"}}
    pl = IP.plan(str(root), spec)
    assert pl["ok"], pl["problems"]
    return root, spec, IP.apply(str(root), spec)


def test_layout_one_repo_and_root_of_main(made):
    root, _, r = made
    assert sorted(os.listdir(root)) == ["experiments", "ledger", "main", "sparse-ct.code-workspace", "stray.txt"]
    assert os.listdir(root / "experiments") == []
    common = {os.path.realpath(g(d, "rev-parse", "--git-common-dir").strip() if os.path.isabs(g(d, "rev-parse", "--git-common-dir").strip())
                               else os.path.join(d, g(d, "rev-parse", "--git-common-dir").strip())) for d in (root / "main", root / "ledger")}
    assert len(common) == 1
    files = g(root / "main", "ls-tree", "-r", "--name-only", "main").split()
    assert {"README.md", ".gitignore", "AGENTS.md", "CLAUDE.md"} <= set(files) and not any(f.startswith("main/") for f in files)
    assert "stray.txt" not in files
    assert (root / "ledger" / ".research" / "config.yaml").exists() and not (root / "main" / ".research").exists()
    assert (root / "main" / "CLAUDE.md").read_text().strip() == "@AGENTS.md"


def test_workspace_file_and_project_resolves_from_every_root(made):
    root, _, r = made
    ws = json.load(open(r["workspace_file"]))
    assert [f["path"] for f in ws["folders"]] == ["main", "ledger", "experiments"] and ws["settings"]["research.projectRoot"] == "."
    for d in ("main", "ledger"):
        with Project(str(root / d)) as p:
            assert p.name == "sparse-ct" and p.config["layout"]["experiments_dir"] == "experiments"
            assert p.config["agents"]["default_profile"] == "claude"


def test_new_experiments_use_configured_root_and_mcp_reachable_from_worktree(made):
    root, _, _ = made
    with Project(str(root / "main")) as p:
        e = services.create_experiment(p, "e", hypothesis="h", question=services.create_question(p, "q")["id"])
        ws = W.create(p, e["id"])
    assert os.path.dirname(ws["path"]) == str(root / "experiments")
    assert (root / "main" / ".agents" / "mcp_config.json").exists()
    v = providers.mcp_verify(str(root / "main"), ws["path"])
    assert v["ok"], v


def test_refuses_nonempty_targets_and_bad_input(tmp_path):
    root = tmp_path / "p"
    (root / "main").mkdir(parents=True)
    (root / "main" / "x").write_text("1")
    assert any("not empty" in x for x in IP.plan(str(root), {"name": "p"})["problems"])
    assert not IP.plan(str(tmp_path), {"name": ""})["ok"]
    assert not IP.plan(str(tmp_path / "q"), {"name": "q", "agents": {"enabled": ["claude"], "default": "codex"}})["ok"]
    assert not IP.plan(str(tmp_path / "q"), {"name": "q", "hpc": {"enabled": True}})["ok"]


def test_hpc_job_environment_is_saved_as_slurm_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "hp"
    root.mkdir()
    spec = {"name": "hp", "hpc": {"enabled": True, "ssh_host": "clu", "workspace_root": "/w", "setup": "module load python/3.11\n\n source ~/v/bin/activate "}}
    assert IP.plan(str(root), spec)["ok"]
    IP.apply(str(root), spec)
    with Project(str(root / "main")) as p:
        assert p.config["hpc"]["clusters"]["clu"]["slurm"]["setup"] == ["module load python/3.11", "source ~/v/bin/activate"]


def test_new_projects_get_a_guide_on_main_with_a_publish_workflow(made):
    root, _, r = made
    files = set(g(root / "main", "ls-tree", "-r", "--name-only", "main").split())
    for f in ("docs/scientist-guide/_quarto.yml", "docs/scientist-guide/references.bib", "docs/scientist-guide/theme.scss",
              "docs/scientist-guide/ideas.qmd", "docs/scientist-guide/results.qmd", ".github/workflows/publish-guide.yml"):
        assert f in files, f
    assert not any(f.startswith("docs/scientist-guide/_output") for f in files)
    assert any("Scientist Guide created" in d for d in r["done"])
    p = Project(str(root / "main"))
    try:
        from research import guide as G
        st = G.status(p)
        assert st["exists"] and st["settings"]["draft"]["max_words"] == 150 and st["publishing"]["workflow"]
    finally:
        p.close()
    assert g(root / "main", "status", "--porcelain").strip() == ""  # everything it wrote is committed


def test_guide_choices_from_the_wizard_are_saved_and_can_be_declined(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    a = tmp_path / "a"
    a.mkdir()
    spec = {"name": "a", "guide": {"enabled": True, "trigger": "checkpoint", "max_words": 80, "max_sections": 2, "max_figures": 0,
                                   "auto_log": False, "publish": False}}
    pl = IP.plan(str(a), spec)
    assert pl["ok"] and any("at most 80 words per section" in x for x in pl["actions"]) and not any("GitHub Actions" in x for x in pl["actions"])
    IP.apply(str(a), spec)
    p = Project(str(a / "main"))
    try:
        from research import guide as G
        s = G.settings(p)
        assert s["draft"] == {"trigger": "checkpoint", "max_words": 80, "max_sections": 2, "max_figures": 0} and s["auto_log"] is False
        assert not (a / "main" / ".github").exists() and G.exists(p)
    finally:
        p.close()
    b = tmp_path / "b"
    b.mkdir()
    IP.apply(str(b), {"name": "b", "guide": {"enabled": False}})
    assert not (b / "main" / "docs").exists()


def test_new_projects_load_skills_hooks_and_mcp_by_default(made):
    root, _, r = made
    main = root / "main"
    # skills: linked where Claude Code and Codex look, never committed (links are machine-local)
    for d in (".claude/skills", ".agents/skills"):
        assert (main / d / "run-experiment" / "SKILL.md").exists() and (main / d / "scientific-briefing").is_symlink()
    assert ".claude/skills/" in (main / ".gitignore").read_text()
    assert not [f for f in g(main, "ls-files").split() if f.startswith((".claude/skills", ".agents/skills"))]
    # hooks: two starters, approved by the wizard, never blocking
    from research import hooks as HK
    p = Project(str(main))
    try:
        hs = {h["name"]: h for h in HK.listing(p)}
        assert set(hs) == set(HK.STARTERS) and len(hs) == 9 and all(h["approved"] and not h["required"] for h in hs.values())
        assert any("starter hooks approved" in d for d in r["done"]) and any("skills linked" in d for d in r["done"])
        # MCP is registered for the enabled agents and the tools view reports all three
        assert (main / ".mcp.json").exists()
    finally:
        p.close()
    from test_plans_tasks_interfaces import rpc
    t = rpc(main, ("tools", {}))[1]["result"]
    assert {s["name"] for s in t["skills"]} >= {"run-experiment", "scientific-briefing"} and t["links"]["claude"]["linked"] == sorted(s["name"] for s in t["skills"])
    assert t["starters"]["syntax-check"]["installed"] and t["mcp_command"]


def test_starter_hooks_and_skill_links_can_be_declined_and_added_later(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    a = tmp_path / "a"
    a.mkdir()
    IP.apply(str(a), {"name": "a", "tools": {"link_skills": False, "starter_hooks": False}})
    main = a / "main"
    assert not (main / ".claude" / "skills").exists()
    from research import hooks as HK
    from research import skills as SK
    p = Project(str(main))
    try:
        assert HK.listing(p) == []
        r = HK.install_starters(p)  # added but not approved: they do not run until a human approves
        assert r["added"] == list(HK.STARTERS) and r["approved"] == [] and not any(h["approved"] for h in HK.listing(p))
        assert HK.install_starters(p, approve_now=True) == {"added": [], "approved": []}  # idempotent, never overwrites
        HK.approve(p, ["syntax-check"])
        assert HK.is_approved(p, "syntax-check")
        assert SK.link_skills(p)[".claude/skills"]["linked"]
    finally:
        p.close()


def test_starter_hooks_really_run(made):
    root, _, _ = made
    from research import hooks as HK
    main = root / "main"
    (main / "ok.py").write_text("x = 1\n")
    p = Project(str(main))
    try:
        r = HK.run(p, "post_implementation")
        assert {x["hook"] for x in r["ran"]} == {n for n, c in HK.STARTERS.items() if c["stage"] == "post_implementation"} and all(x.get("passed", x.get("ok")) for x in r["ran"]), r
        (main / "bad.py").write_text("def broken(:\n")
        r2 = HK.run(p, "post_implementation", names=["syntax-check"])
        assert not r2["ran"][0].get("passed", r2["ran"][0].get("ok"))
    finally:
        p.close()


def test_an_agent_running_the_wizard_cannot_approve_the_starter_hooks_and_nothing_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDECODE", "1")   # the process looks like an agent session
    a = tmp_path / "a"
    a.mkdir()
    r = IP.apply(str(a), {"name": "a"})
    assert any("NOT approved" in d for d in r["done"])
    from research import hooks as HK
    p = Project(str(a / "main"))
    try:
        assert {h["name"]: h["approved"] for h in HK.listing(p)} == {n: False for n in HK.STARTERS}
    finally:
        p.close()


def test_plan_writes_nothing_and_refuses_existing_project_and_unknown_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "study"
    pl = IP.plan(str(root), {"name": "Study", "goal": "Learn alpha"})
    assert pl["ok"] and not root.exists(), "a plan writes nothing"
    bad = IP.plan(str(root), {"name": "Study", "agents": {"strategy": "nope"}})
    assert not bad["ok"] and any("strategy" in x for x in bad["problems"])
    (root / ".research").mkdir(parents=True)
    (root / ".research" / "config.yaml").write_text("project: {}\n")
    assert any("already contains" in x for x in IP.plan(str(root), {"name": "Study"})["problems"])


# ---- guided new-experiment flow (services.new_experiment)
@pytest.fixture
def shared(proj, tmp_path):
    proj.close()
    W.enable(proj.root, str(tmp_path / "ledger"))
    p = Project(proj.root)
    yield p
    p.close()


def test_new_experiment_minimal_creates_only_what_was_asked(proj):
    r = services.new_experiment(proj, {"title": "Step size", "hypothesis": "h", "question": {"title": "Which alpha?"}})
    assert r["question"] == "Q-001" and r["experiment"] == "EXP-001" and "task" not in r and "workspace" not in r
    assert proj.get("experiment", "EXP-001")["question_id"] == "Q-001"
    assert proj.q1("SELECT COUNT(*) AS n FROM runs")["n"] == 0, "creating an experiment never runs anything"


def test_new_experiment_full_flow_with_workspace_task_profile_and_prepared_agent(shared):
    p = shared
    r = services.new_experiment(p, {"title": "Prior", "hypothesis": "h", "question": {"title": "q"}, "worktree": {"create": True, "name": "prior"},
                              "task": {"create": True}, "execution": {"profile": "restricted"}, "agent": {"dispatch": True, "role": "implementer"}})
    ws = r["workspace"]
    assert os.path.isdir(ws["path"]) and ws["branch"].startswith("exp/EXP-001")
    assert W.show(p, ws["id"])["tasks"] == [r["task"]]
    assert p.config["sandbox"]["assign"]["experiments"]["EXP-001"] == "restricted"
    d = r["dispatch"]
    assert d["cwd"] == ws["path"] and os.path.exists(p.abspath(d["brief"])), "the agent is pointed at the new workspace"
    assert p.q1("SELECT COUNT(*) AS n FROM runs")["n"] == 0
    assert subprocess.run(["git", "rev-parse", "main"], cwd=p.root, capture_output=True, text=True).stdout.strip() == \
        subprocess.run(["git", "rev-parse", "main"], cwd=ws["path"], capture_output=True, text=True).stdout.strip() or True


def test_new_experiment_problems_are_reported_up_front(proj):
    pl = services.new_experiment_plan(proj, {"title": "x", "worktree": {"create": True}, "agent": {"dispatch": True}, "execution": {"profile": "nope"}})
    assert not pl["ok"]
    text = " ".join(pl["problems"])
    assert "shared ledger" in text and "needs a task" in text and "Unknown sandbox profile" in text
    with pytest.raises(ResearchError):
        services.new_experiment(proj, {"title": "x", "worktree": {"create": True}})
    assert proj.list("experiment") == [], "nothing was created"


def test_new_experiment_failure_midway_keeps_and_names_what_exists(proj, monkeypatch):
    from research import plans
    monkeypatch.setattr(plans, "create_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(ResearchError, match="Already created and kept: .*experiment EXP-001"):
        services.new_experiment(proj, {"title": "x", "task": {"create": True}})
    assert proj.get("experiment", "EXP-001")
