"""Skill packs, the catalog (integrity, licensing, attribution), activation, project-aware hooks and their checks."""
import json
import os
import re
import subprocess

import pytest

from research import checks as CK
from research import hooks as HK
from research import orchestrate as O
from research import packs as PK
from research import plans, services as S
from research import skills as SK
from research.store import TEMPLATES, Project
from research.util import NotFound, PolicyBlocked, ResearchError
from research._vendor import yaml

KNOWN_ACTIVATION = {"research", "implementation", "experiment", "analysis", "verification", "synthesis", "debug", "data",
                    "planner", "implementer", "verifier", "analyst", "hpc", "frontend"}
REDISTRIBUTABLE = {"MIT", "Apache-2.0"}


def fm(path):
    t = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n", t, re.S)
    assert m, path
    return yaml.safe_load(m.group(1))


# ------------------------------------------------------------------ the catalog is sound

def test_catalog_entries_are_complete_unique_and_resolve_to_files():
    cat = PK.catalog()
    names = [e["name"] for e in cat]
    assert len(names) == len(set(names)) and len(names) >= 50
    for e in cat:
        assert e["kind"] in ("first-party", "bundled", "external") and e["description"] and e["license"] and e["author"], e["name"]
        assert set(e.get("applies_to") or []) <= KNOWN_ACTIVATION, (e["name"], e["applies_to"])
        if e["kind"] == "external":
            assert re.fullmatch(r"[0-9a-f]{40}", e["commit"]) and e["source"].startswith("https://github.com/") and e["source_path"], e["name"]
            assert not (TEMPLATES / "catalog" / e["name"]).exists(), "external skills are not shipped"
        else:
            d = TEMPLATES / e["path"]
            assert (d / "SKILL.md").is_file(), e["name"]
            meta = fm(d / "SKILL.md")  # strict YAML: an unquoted colon in a description breaks other agent tools
            assert str(meta["name"]).strip() == e["name"] and 20 < len(str(meta["description"])) < 1500, e["name"]


def test_bundled_skills_carry_licence_attribution_and_a_pinned_commit():
    for e in PK.catalog():
        if e["kind"] != "bundled":
            continue
        d = TEMPLATES / e["path"]
        src = yaml.safe_load((d / "SOURCE.yaml").read_text())
        assert e["license"] in REDISTRIBUTABLE, f"{e['name']}: {e['license']} cannot be bundled"
        assert re.fullmatch(r"[0-9a-f]{40}", src["commit"]) and src["commit"] == e["commit"] and src["repo"].startswith("https://github.com/")
        lic = (d / "LICENSE").read_text()
        assert ("Apache License" in lic) if e["license"] == "Apache-2.0" else ("Permission is hereby granted" in lic)
        assert src["copyright"] and src["retrieved"] and src["modified"]


def test_share_alike_or_unlicensed_sources_are_never_bundled():
    for e in PK.catalog():
        if e["kind"] == "bundled":
            assert "SA" not in e["license"] and e["license"] != "no license published"
    ext = {e["name"]: e for e in PK.catalog() if e["kind"] == "external"}
    assert ext["modern-python"]["license"].startswith("CC-BY-SA") and ext["web-design-guidelines"]["license"] == "no license published"


def test_packs_reference_real_skills_and_cover_the_requested_fields():
    names = {e["name"] for e in PK.catalog()}
    ids = [x["id"] for x in PK.pack_defs()]
    assert ids == ["core-research", "scientific-python", "machine-learning", "physics-numerical", "medical-imaging", "quantum-computing", "hpc", "frontend", "scientific-writing"]
    assert PK.default_pack_ids() == ["core-research"]
    for x in PK.pack_defs():
        assert set(x["skills"]) | set(x.get("optional") or []) <= names, x["id"]
        assert all(PK.entry(n)["kind"] != "external" for n in x["skills"]), "a pack's own skills must work offline"
    core = set(PK.pack("core-research")["skills"])
    ten = {"research-context", "hypothesis-design", "validation-oracles", "failure-triage", "reproducibility-audit", "uncertainty-analysis",
           "literature-evidence", "scientific-visualization", "independent-review", "promote-validated-work"}
    assert ten <= core and {"systematic-debugging", "verification-before-completion", "experimental-design"} <= core
    assert {e["name"] for e in PK.catalog() if e["kind"] == "first-party"} == {d.name for d in (TEMPLATES / "skills").iterdir()}


def test_first_party_skills_use_real_commands_and_do_not_duplicate_each_other():
    root = os.path.join(os.path.dirname(__file__), "..", "..", "packages", "cli", "research_cli")
    cli = "".join(open(os.path.join(dp, f), encoding="utf-8").read() for dp, _, fs in os.walk(root) for f in fs if f.endswith(".py"))
    for d in (TEMPLATES / "skills").iterdir():
        t = (d / "SKILL.md").read_text()
        for m in re.finditer(r"`research ([a-z-]+)(?: ([a-z-]+))?", t):
            assert f'"{m.group(1)}"' in cli or m.group(1) in ("skills", "skill", "promote"), (d.name, m.group(0))
    descs = [PK.entry(n)["description"] for n in ("hypothesis-design", "experimental-design", "validation-oracles", "independent-review", "peer-review")]
    assert len(set(descs)) == 5


# ------------------------------------------------------------------ installing, enabling, updating

def test_a_new_project_gets_the_core_pack_and_its_activation_rules(proj, repo):
    have = {s["name"] for s in SK.list_skills(proj)}
    assert set(PK.pack("core-research")["skills"]) <= have
    m = SK.load_manifest(proj)
    assert m["validation-oracles"]["applies_to"] == ["implementation", "verification", "verifier"] and m["research-context"]["always"] is True
    assert m["systematic-debugging"]["origin"] == "bundled" and m["systematic-debugging"]["license"] == "MIT" and len(m["systematic-debugging"]["commit"]) == 40
    assert PK.enabled_pack_ids(proj) == ["core-research"]


def test_skills_activate_by_task_type_and_role_and_are_not_all_injected(proj):
    plan = plans.create_plan(proj, "p", objective="o")
    dbg = plans.create_task(proj, plan["id"], "fix it", task_type="debug", assigned_role="implementer")
    got = {s["name"] for s in SK.skills_for(proj, proj.get("task", dbg["id"]))}
    assert {"systematic-debugging", "failure-triage", "research-context", "engineering-loop", "verification-before-completion"} <= got
    assert not got & {"hypothesis-design", "literature-evidence", "independent-review", "uncertainty-analysis"}   # not for a debug task
    assert len(got) < len(SK.list_skills(proj)) / 2
    ver = plans.create_task(proj, plan["id"], "check it", task_type="verification", assigned_role="verifier")
    got2 = {s["name"] for s in SK.skills_for(proj, proj.get("task", ver["id"]))}
    assert {"independent-review", "validation-oracles", "reproducibility-audit"} <= got2 and "systematic-debugging" not in got2


def test_enabling_a_pack_installs_attributed_copies_and_never_touches_your_files(proj):
    mine = proj.path("skills", "matplotlib")
    os.makedirs(mine)
    open(os.path.join(mine, "SKILL.md"), "w").write("---\nname: matplotlib\ndescription: my own plotting rules\n---\nmine\n")
    r = PK.enable_pack(proj, "scientific-python")
    states = {x["name"]: x["state"] for x in r["results"]}
    assert states["matplotlib"] == "kept" and states["statistical-analysis"] == "installed"
    assert "mine" in open(os.path.join(mine, "SKILL.md")).read()
    assert os.path.exists(proj.path("skills", "statistical-analysis", "LICENSE")) and os.path.exists(proj.path("skills", "statistical-analysis", "SOURCE.yaml"))
    assert PK.enabled_pack_ids(proj) == ["core-research", "scientific-python"]
    assert "scientific-python" in open(proj.config_path).read()          # files are the source of truth
    st = {x["name"]: x for x in PK.state(proj)["catalog"]}
    assert st["matplotlib"]["installed"] and st["statistical-analysis"]["in_enabled_pack"] and not st["qiskit"]["installed"]


def test_disable_pack_and_skill_keep_files_but_stop_suggesting_and_linking(proj, repo):
    PK.enable_pack(proj, "quantum-computing")
    assert os.path.islink(repo / ".claude" / "skills" / "qiskit") if (repo / ".claude" / "skills").exists() else True
    SK.link_skills(proj)
    assert (repo / ".claude" / "skills" / "qiskit").exists()
    r = PK.disable_pack(proj, "quantum-computing")
    assert "qiskit" in r["disabled"] and os.path.isdir(proj.path("skills", "qiskit"))      # files kept
    assert not (repo / ".claude" / "skills" / "qiskit").exists()
    plan = plans.create_plan(proj, "p", objective="o")
    t = plans.create_task(proj, plan["id"], "circuit", task_type="implementation")
    assert "qiskit" not in {s["name"] for s in SK.skills_for(proj, proj.get("task", t["id"]))}
    PK.set_enabled(proj, "qiskit", True)
    assert (repo / ".claude" / "skills" / "qiskit").exists() and "qiskit" in {s["name"] for s in SK.skills_for(proj, proj.get("task", t["id"]))}
    with pytest.raises(ResearchError, match="cannot be turned off"):
        PK.disable_pack(proj, "core-research")
    with pytest.raises(NotFound):
        PK.enable_pack(proj, "no-such-pack")


def test_update_replaces_untouched_copies_keeps_yours_and_adds_missing_first_party(proj):
    # an old project: one first-party skill missing, one bundled skill edited, one bundled skill from an older release
    import shutil
    shutil.rmtree(proj.path("skills", "failure-triage"))
    m = SK.load_manifest(proj)
    del m["failure-triage"]
    SK.save_manifest(proj, m)
    open(proj.path("skills", "systematic-debugging", "SKILL.md"), "a").write("\nmy note\n")
    open(proj.path("skills", "experimental-design", "SKILL.md"), "a").write("\n<!-- stale -->\n")
    m = SK.load_manifest(proj)
    m["experimental-design"]["installed_hash"] = PK.digest_dir(proj.path("skills", "experimental-design"))   # looks pristine, but differs from the release
    m["experimental-design"]["commit"] = "0" * 40
    SK.save_manifest(proj, m)
    r = PK.update(proj)
    assert "failure-triage (new)" in r["updated"] and "experimental-design" in r["updated"] and r["kept_yours"] == ["systematic-debugging"]
    assert "my note" in open(proj.path("skills", "systematic-debugging", "SKILL.md")).read()
    assert "stale" not in open(proj.path("skills", "experimental-design", "SKILL.md")).read()
    assert PK.update(proj)["updated"] == []     # idempotent


def test_a_forced_install_keeps_your_changed_copy_in_the_cache(proj):
    PK.install_skill(proj, "ponytail-review")
    open(proj.path("skills", "ponytail-review", "SKILL.md"), "a").write("\nmine\n")
    assert PK.install_skill(proj, "ponytail-review")["state"] == "kept"
    assert PK.install_skill(proj, "ponytail-review", force=True)["state"] == "installed"
    assert "mine" not in open(proj.path("skills", "ponytail-review", "SKILL.md")).read()
    assert any("ponytail-review" in x for x in os.listdir(proj.path("cache", "removed-skills")))


def test_external_skills_install_from_a_pinned_commit_and_say_so_when_offline(proj, tmp_path, monkeypatch):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    up = tmp_path / "upstream"
    (up / "skills" / "web-design-guidelines").mkdir(parents=True)
    (up / "skills" / "web-design-guidelines" / "SKILL.md").write_text("---\nname: web-design-guidelines\ndescription: upstream copy\n---\nbody\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=up, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=up, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=up, check=True, env=env)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=up, capture_output=True, text=True, check=True).stdout.strip()
    e = dict(PK.entry("web-design-guidelines"), commit=sha)
    real = PK.entry
    monkeypatch.setattr(PK, "entry", lambda n: e if n == "web-design-guidelines" else real(n))
    monkeypatch.setattr(PK, "_clone_url", lambda _e: str(up))
    r = PK.install_skill(proj, "web-design-guidelines", "frontend")
    assert r["state"] == "installed from source" and "upstream copy" in open(proj.path("skills", "web-design-guidelines", "SKILL.md")).read()
    rec = SK.load_manifest(proj)["web-design-guidelines"]
    assert rec["commit"] == sha and rec["origin"] == "external" and rec["license"] == "no license published"
    monkeypatch.setattr(PK, "_clone_url", lambda _e: str(tmp_path / "nowhere"))
    with pytest.raises(ResearchError) as bad:
        PK.install_skill(proj, "react-best-practices")
    assert "Could not fetch react-best-practices" in str(bad.value) and "network" in (bad.value.hint or "")


def test_packs_and_disabled_skills_follow_the_machine_file_then_the_project_file(tmp_path, monkeypatch, repo):
    xdg = tmp_path / "xdg2"
    (xdg / "research-os").mkdir(parents=True)
    (xdg / "research-os" / "config.yaml").write_text("skills:\n  packs: [core-research, machine-learning]\n  disabled: [shap]\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    p = Project.init(str(repo), name="m")
    try:
        assert PK.enabled_pack_ids(p) == ["core-research", "machine-learning"] and PK.disabled_names(p) == ["shap"]
        assert os.path.isdir(p.path("skills", "pytorch-lightning"))      # a new project installs this computer's default packs
        p.save_config({"skills.packs": ["core-research"]})
        p.config = p.load_config()
        assert PK.enabled_pack_ids(p) == ["core-research"]               # the project value wins
        from research import registry
        keys = {o["key"] for o in registry.describe(p)["options"]}
        assert {"skills.packs", "skills.disabled", "harness.require_seed"} <= keys
    finally:
        p.close()


def test_cli_catalog_packs_install_enable_and_upgrade(proj, repo, capsys):
    from research_cli.main import main
    proj.close()
    run = lambda *a: main(["--root", str(repo), *a])  # noqa: E731
    assert run("skills", "catalog", "--search", "statistics") == 0 and "statistical-analysis" in capsys.readouterr().out
    assert run("skills", "catalog", "--pack", "quantum-computing", "--json") == 0
    assert {x["name"] for x in json.loads(capsys.readouterr().out)} == {"qiskit", "cirq", "pennylane", "sympy"}
    assert run("skills", "packs") == 0 and "core-research" in capsys.readouterr().out
    assert run("skills", "packs", "enable", "medical-imaging") == 0 and "pydicom installed" in capsys.readouterr().out
    assert run("skills", "disable", "pydicom") == 0 and run("skills", "enable", "pydicom") == 0
    assert run("skills", "install", "cirq") == 0 and "cirq: installed" in capsys.readouterr().out
    assert run("skills", "upgrade") == 0 and "current" in capsys.readouterr().out
    assert run("skills", "packs", "disable", "core-research") != 0


# ------------------------------------------------------------------ project-aware hooks

def test_stack_detection_reads_marker_files_only(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["numpy", "torch"]\n')
    (tmp_path / "tsconfig.json").write_text("{}")
    (tmp_path / "x.ipynb").write_text("{}")
    got = HK.detect_stacks(str(tmp_path))
    assert {"python", "typescript", "notebooks", "scientific", "ml"} <= set(got) and "quantum" not in got
    assert HK.detect_stacks(str(tmp_path / "missing")) == []


def test_recommended_is_project_aware_and_cheap_ones_are_defaults(proj, repo):
    r = HK.recommended(proj)
    by = {h["name"]: h for h in r["hooks"]}
    assert by["tests"]["tier"] == "costly" and by["tsc"]["tier"] == "costly" and not by["tests"]["default"]
    assert all(by[n]["default"] and by[n]["tier"] == "cheap" for n in HK.STARTERS) and len(HK.STARTERS) == 9
    assert not by["tsc"]["applies"]                                        # no TypeScript here
    (repo / "tsconfig.json").write_text("{}")
    (repo / "pyproject.toml").write_text('[project]\ndependencies = ["numpy"]\n')
    r2 = {h["name"]: h for h in HK.recommended(proj)["hooks"]}
    assert r2["tsc"]["applies"] and r2["lint"]["applies"] and r2["format"]["applies"]
    added = HK.install(proj)["added"]                                      # project-aware set: cheap hooks that apply
    assert "lint" in added and "format" in added and "tests" not in added and "tsc" not in added
    assert HK.install(proj, ["tests"])["added"] == ["tests"] and not HK.is_approved(proj, "tests")   # costly: added, never approved for you
    with pytest.raises(ResearchError):
        HK.install(proj, ["nope"])


def test_every_hook_stays_unapproved_until_a_human_approves_it_and_edits_revoke(proj):
    HK.install_starters(proj)
    assert not any(h["approved"] for h in HK.listing(proj))
    assert HK.run(proj, "post_implementation")["ran"] == []
    HK.approve(proj, ["secrets"])
    assert [r["hook"] for r in HK.run(proj, "post_implementation")["ran"]] == ["secrets"]
    proj.save_config({"harness.hooks.secrets.command": "true"})
    proj.config = proj.load_config()
    assert not HK.is_approved(proj, "secrets")


# ------------------------------------------------------------------ the checks themselves

def put(repo, name, text):
    (repo / name).write_text(text)


def test_secrets_check_finds_keys_without_printing_them_and_honours_the_allow_marker(repo):
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    put(repo, "leak.py", f"KEY = '{key}'\n")
    put(repo, "ok.py", f"KEY = '{key}'  # research:allow-secret\n")
    put(repo, "pem.txt", "-----BEGIN RSA " + "PRIVATE KEY-----\nabc\n")
    put(repo, "tok.py", "TOKEN = 'ghp_" + "a" * 36 + "'\npassword = 'correct-horse-battery-staple'\n")
    put(repo, "clean.py", "x = 1\n")
    ok, lines = CK.secrets(str(repo))
    out = "\n".join(lines)
    assert not ok and "leak.py:1" in out and "pem.txt:1" in out and "tok.py:1" in out and "tok.py:2" in out
    assert not any(l.startswith(("ok.py", "clean.py")) for l in lines) and key not in out and "ghp_" not in out and "correct-horse" not in out


def test_git_integrity_check_flags_unfinished_merges(repo):
    ok, _ = CK.git_integrity(str(repo))
    assert ok
    git_dir = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=repo, capture_output=True, text=True).stdout.strip()
    open(os.path.join(git_dir, "MERGE_HEAD"), "w").write("0" * 40 + "\n")
    ok, lines = CK.git_integrity(str(repo))
    assert not ok and any("merge is in progress" in x for x in lines)


def test_lint_format_tests_and_tsc_skip_honestly_when_the_tool_is_missing(repo, monkeypatch):
    put(repo, "a.py", "import os\nx=1\n")
    monkeypatch.setattr(CK.shutil, "which", lambda n: None)
    monkeypatch.setattr(CK, "_module", lambda n: False)
    assert CK.lint(str(repo)) == (True, [CK.SKIP + "neither ruff nor pyflakes is installed (`uv pip install ruff`)"])
    assert CK.format_check(str(repo))[1][0].startswith(CK.SKIP)
    assert CK.tests(str(repo))[1][0].startswith(CK.SKIP)          # no tests dir here
    assert CK.tsc(str(repo))[1][0].startswith(CK.SKIP)
    (repo / "tsconfig.json").write_text("{}")
    assert "nothing is downloaded" in CK.tsc(str(repo))[1][0]


def test_lint_uses_pyflakes_when_available_and_tests_run_pytest_only_on_request(repo):
    pytest.importorskip("pyflakes")
    put(repo, "bad.py", "import os\n")
    ok, lines = CK.lint(str(repo))
    assert not ok and "unused" in lines[0]
    (repo / "tests").mkdir()
    put(repo, "tests/test_a.py", "def test_a():\n    assert 1 == 2\n")
    ok, _ = CK.tests(str(repo))
    assert not ok


def test_task_scope_and_frozen_tests_checks(proj, repo, monkeypatch):
    plan = plans.create_plan(proj, "p", objective="o")
    t = plans.create_task(proj, plan["id"], "impl", task_type="implementation", acceptance_criteria="a", checks=["cmd:true"])
    tid = t["id"]
    git_env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=git_env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True, env=git_env)      # what exists before the task is not the task's change
    monkeypatch.setenv("RESEARCH_TASK", tid)
    assert CK.task_scope(str(repo))[1][0].startswith(CK.SKIP) and CK.frozen_tests(str(repo))[1][0].startswith(CK.SKIP)
    put(repo, "test_x.py", "def test_a():\n    assert 1\n")
    O.freeze(proj, tid, ["test_x.py"])
    O.set_contract(proj, tid, allowed_files=["src/*", "test_x.py"])
    assert CK.frozen_tests(str(repo)) == (True, ["frozen acceptance tests are intact"])
    (repo / "src").mkdir()
    put(repo, "src/ok.py", "x = 1\n")
    assert CK.task_scope(str(repo))[0], CK.task_scope(str(repo))
    put(repo, "elsewhere.py", "y = 2\n")
    ok, lines = CK.task_scope(str(repo))
    assert not ok and "elsewhere.py" in lines[0] and O.scope_violations(proj, tid) == ["elsewhere.py"]   # the same rule verification uses
    put(repo, "test_x.py", "def test_a():\n    assert 0\n")
    ok, lines = CK.frozen_tests(str(repo))
    assert not ok and "test_x.py" in lines[0]


def _run_with(proj, repo, **kw):
    from research import runs as R
    e = S.create_experiment(proj, "e")["id"]
    return e, R.create_run(proj, e, command=kw.get("command", "python x.py"), status="completed", parameters=kw.get("parameters"))


def test_experiment_config_check_requires_clean_recorded_runs(proj, repo, monkeypatch):
    assert CK.experiment_config(str(repo))[1][0].startswith(CK.SKIP)
    e, r = _run_with(proj, repo, parameters={"alpha": 0.5})
    proj.conn.execute("UPDATE runs SET git_commit='abc', git_dirty=0 WHERE id=?", (r["id"],))
    proj.conn.commit()
    ok, lines = CK.experiment_config(str(repo))
    assert ok and any("no seed" in x for x in lines)                      # a warning, not an error by default
    proj.save_config({"harness.require_seed": True})
    assert not CK.experiment_config(str(repo))[0]
    proj.conn.execute("UPDATE runs SET parameters=?, git_dirty=1 WHERE id=?", (json.dumps({"seed": 3}), r["id"]))
    proj.conn.commit()
    ok, lines = CK.experiment_config(str(repo))
    assert not ok and "dirty working tree" in lines[0]
    proj.conn.execute("UPDATE runs SET git_dirty=0 WHERE id=?", (r["id"],))
    proj.conn.commit()
    assert CK.experiment_config(str(repo))[0]


def test_numeric_invariants_check_flags_nan_inf_and_declared_bounds(proj, repo):
    from research import runs as R
    assert CK.numeric_invariants(str(repo))[1][0].startswith(CK.SKIP)
    e, r = _run_with(proj, repo)
    R.log_metric(proj, "rmse", 12.5, run=r["id"])
    assert CK.numeric_invariants(str(repo))[0]
    proj.conn.execute("INSERT INTO metrics(run_id, experiment_id, name, value, value_text) VALUES (?,?,?,?,?)", (r["id"], e, "loss", None, "nan"))
    proj.conn.commit()
    ok, lines = CK.numeric_invariants(str(repo))
    assert not ok and "loss = nan" in lines[0]
    proj.conn.execute("DELETE FROM metrics WHERE name='loss'")
    proj.conn.commit()
    proj.save_config({"harness.invariants": {"rmse": {"min": 0, "max": 10}}})
    ok, lines = CK.numeric_invariants(str(repo))
    assert not ok and "above its maximum" in lines[0]


def test_artifact_integrity_check_detects_modified_and_missing_files(proj, repo):
    from research import runs as R
    assert CK.artifact_integrity(str(repo))[1][0].startswith(CK.SKIP)
    e, r = _run_with(proj, repo)
    put(repo, "out.txt", "result 1\n")
    R.register_artifact(proj, str(repo / "out.txt"), run=r["id"])
    assert CK.artifact_integrity(str(repo))[0]
    put(repo, "out.txt", "result 2\n")
    ok, lines = CK.artifact_integrity(str(repo))
    assert not ok and "modified" in lines[0]
    os.remove(repo / "out.txt")
    assert "missing" in CK.artifact_integrity(str(repo))[1][0]


def test_checks_run_as_hooks_from_any_interpreter_and_a_crash_is_a_failure(proj, repo):
    HK.install(proj, ["secrets", "git-integrity"])
    HK.approve(proj)
    put(repo, "leak.py", "KEY = 'AKIA" + "ABCDEFGHIJKLMNOP'\n")
    r = HK.run(proj, "post_implementation")
    assert {x["hook"] for x in r["ran"]} == {"secrets", "git-integrity"}
    assert r["ok"]                                                         # advisory hooks are reported, never blocking
    sec = next(x for x in r["ran"] if x["hook"] == "secrets")
    assert not sec["ok"] and "leak.py:1" in sec["output"] and "AKIA" not in sec["output"]
    env = {**os.environ, "RESEARCH_ROOT": "/nonexistent-root"}
    out = subprocess.run([os.sys.executable, "-m", "research.checks", "task-scope"], env={**env, "RESEARCH_TASK": "T-1"}, capture_output=True, text=True,
                         cwd="/tmp", timeout=60)
    assert out.returncode == 1 and "could not run" in out.stdout
    assert subprocess.run([os.sys.executable, "-m", "research.checks", "bogus"], capture_output=True, text=True, cwd="/tmp").returncode == 2


def test_advisory_hooks_do_not_count_as_a_verifier(proj, repo):
    plan = plans.create_plan(proj, "p", objective="o")
    t = plans.create_task(proj, plan["id"], "impl", task_type="implementation")      # no checks
    HK.install_starters(proj)
    HK.approve(proj)
    r = O.verify_work(proj, t["id"])
    assert not r["passed"] and any("no deterministic verifier ran" in d for d in r["diagnostics"])
    HK.add(proj, "oracle", "post_implementation", "true", None, True, "binding")
    HK.approve(proj, ["oracle"])
    assert O.verify_work(proj, t["id"])["passed"]


# ------------------------------------------------------------------ the wizard, end to end

def test_wizard_installs_chosen_packs_hooks_and_links_then_a_small_workflow_runs(tmp_path, monkeypatch):
    from research import initproject as IP
    from research import runs as R
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDECODE", raising=False)
    root = tmp_path / "lab"
    root.mkdir()
    spec = {"name": "lab", "tools": {"packs": ["core-research", "scientific-python", "medical-imaging"]}, "guide": {"enabled": True}}
    pl = IP.plan(str(root), spec)
    assert pl["ok"] and any("packs: Scientific Python, Medical Imaging" in a for a in pl["actions"]) and any("nine cheap starter hooks" in a for a in pl["actions"])
    bad = IP.plan(str(tmp_path / "other"), {"name": "x", "tools": {"packs": ["bogus"]}})
    assert not bad["ok"] and "Unknown skill pack" in bad["problems"][0]
    res = IP.apply(str(root), spec)
    main = root / "main"
    assert any("skill pack scientific-python: 8 skills" in d for d in res["done"]) and any("starter hooks approved" in d for d in res["done"])
    for d in (".claude/skills", ".agents/skills"):
        for n in ("research-context", "systematic-debugging", "pydicom", "statistical-analysis"):
            assert (main / d / n / "SKILL.md").exists(), (d, n)
    assert not subprocess.run(["git", "ls-files", ".claude/skills"], cwd=main, capture_output=True, text=True).stdout.strip()
    p = Project(str(main))
    try:
        assert PK.enabled_pack_ids(p) == ["core-research", "scientific-python", "medical-imaging"]
        assert {h["name"] for h in HK.listing(p) if h["approved"]} == set(HK.STARTERS)
        # a small research workflow: hypothesis → run → metrics → artifact → hooks → finding → review
        e = S.create_experiment(p, "alpha sweep", hypothesis="alpha 0.5 minimises error")["id"]
        (main / "run.py").write_text("print('x')\n")
        subprocess.run(["git", "add", "-A"], cwd=main, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "code"], cwd=main, check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        r = R.create_run(p, e, command="python run.py", status="completed", parameters={"alpha": 0.5, "seed": 1})
        R.log_metric(p, "rmse", 12.5, run=r["id"])
        (main / "out.txt").write_text("1\n")
        R.register_artifact(p, str(main / "out.txt"), run=r["id"])
        post = HK.run(p, "post_experiment", experiment=e)
        assert {x["hook"] for x in post["ran"]} == {"experiment-config", "numeric-invariants", "artifact-integrity"}
        ok = {x["hook"]: x for x in post["ran"]}
        assert ok["numeric-invariants"]["ok"] and ok["artifact-integrity"]["ok"]
        plan = plans.create_plan(p, "pl", objective="o")
        t = plans.create_task(p, plan["id"], "analyse", task_type="analysis", assigned_role="analyst")
        names = {s["name"] for s in SK.skills_for(p, p.get("task", t["id"]))}
        assert {"uncertainty-analysis", "statistical-analysis", "research-context"} <= names and "qiskit" not in names
    finally:
        p.close()


def test_skill_links_resolve_when_the_project_is_opened_through_a_symlinked_path(tmp_path):
    """macOS: /var and /tmp are symlinks. Links built from lexical paths pointed nowhere (found with the wizard under /var/folders)."""
    real = tmp_path / "real"
    (real / "proj").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=real / "proj", check=True)
    via = tmp_path / "via"
    os.symlink(real, via)
    p = Project.init(str(via / "proj"), name="s")
    try:
        SK.link_skills(p)
        for d in (".claude/skills", ".agents/skills"):
            assert (via / "proj" / d / "research-context" / "SKILL.md").exists(), d
        again = SK.link_skills(p)
        assert again[".claude/skills"]["skipped"] == [] and again[".claude/skills"]["removed"] == []      # idempotent: our links are recognised as ours
    finally:
        p.close()


def test_upgrading_a_2_2_project_adds_the_new_skills_of_its_enabled_packs(proj):
    import shutil
    for n in ("systematic-debugging", "experimental-design", "research-context"):
        shutil.rmtree(proj.path("skills", n))
    m = SK.load_manifest(proj)
    for n in ("systematic-debugging", "experimental-design", "research-context"):
        m.pop(n, None)
    SK.save_manifest(proj, m)
    PK.set_enabled(proj, "experimental-design", False)             # a skill you switched off is not re-added
    r = PK.update(proj)
    assert "systematic-debugging (new)" in r["updated"] and "research-context (new)" in r["updated"] and "experimental-design (new)" not in r["updated"]
    assert not os.path.exists(proj.path("skills", "experimental-design"))
