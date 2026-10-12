"""`research upgrade` and `research agents-md --preview/--diff/--apply`: safety on existing projects.

The contract under test: an upgrade adds what is missing, preserves everything that exists, and is safe to
re-run; a dry run changes nothing at all; and the project's own AGENTS.md bytes are never touched.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from research import agentsmd as AM
from research import fullreport as FR
from research import services as S
from research import upgrade as UP
from research import yamlio
from research.store import Project
from research.util import ResearchError


# --------------------------------------------------------------------------- helpers

def _tree(root, skip=("bin", "cache", "__pycache__")):
    """Every file under the project with its bytes, for exact before/after comparison."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in sorted(dirs) if x not in skip and x != ".git"]
        for fn in sorted(files):
            if fn.startswith("research.db"):
                continue
            path = os.path.join(d, fn)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = f.read()
    return out


def _db_mtime(root):
    db = os.path.join(root, ".research", "research.db")
    return os.path.getmtime(db) if os.path.exists(db) else None


def _records(p):
    """Logical research-record inventory, independent of serialization.

    `events` is excluded on purpose: the audit log is append-only and an upgrade *adds* to it (that is the
    auditability requirement). `_events_only` asserts it never shrinks.
    """
    out = {}
    for table in ("questions", "experiments", "runs", "metrics", "artifacts", "syntheses", "findings",
                  "decisions", "checkpoints", "plans", "tasks", "reviews", "links"):
        out[table] = p.q(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
    return out


def _events_only(p):
    return p.q("SELECT COUNT(*) AS n FROM events")[0]["n"]


USER_PROSE = """# Agent instructions — my project

## House rules (mine)

Always use float64. Never touch `data/raw/`.

"""
USER_TAIL = """

## More of my own notes

Numbers must come from `results/`, never from memory.
"""


@pytest.fixture
def legacy(proj):
    """An existing project with records, custom config, custom AGENTS.md prose and hand-edited Markdown."""
    p = proj
    q = S.create_question(p, "legacy question")
    e = S.create_experiment(p, "legacy experiment", question=q["id"], hypothesis="h")
    from research import runs as R
    r = R.attach_run(p, e["id"], command="python old.py", status="completed")
    R.log_metric(p, "rmse", 0.3, run=r["id"])
    S.synthesize(p, e["id"], what_happened="it ran")
    S.create_finding(p, "legacy finding", supports=[e["id"]])
    S.create_decision(p, "legacy decision")
    S.create_note(p, "legacy note", "hand written")
    # a project-specific config value and a key this version does not know about
    p.config["project"]["goal"] = "my own goal"
    p.config["my_lab"] = {"cluster": "duke", "quota_gb": 500}
    p.save_config()
    # user-authored AGENTS.md prose around the managed block
    with open(os.path.join(p.root, "AGENTS.md"), encoding="utf-8") as f:
        managed = f.read()
    block = managed[managed.index(AM.BEGIN):managed.index(AM.END) + len(AM.END)]
    with open(os.path.join(p.root, "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write(USER_PROSE + block + USER_TAIL)
    # a hand edit to canonical Markdown
    mirror = p.mirror_path("question", q["id"])
    with open(mirror, encoding="utf-8") as f:
        md = f.read()
    with open(mirror, "w", encoding="utf-8") as f:
        f.write(md.replace("## Description\n", "## Description\n\nEdited by hand, with ünïcode.\n"))
    p.sync(reconcile_runs=False)
    return p


# --------------------------------------------------------------------------- dry run changes nothing

def test_dry_run_mutates_nothing(legacy):
    root = legacy.root
    legacy.close()
    before, db_before = _tree(root), _db_mtime(root)
    r = UP.plan(root)
    assert _tree(root) == before
    assert _db_mtime(root) == db_before
    assert "steps" in r and r["note"]


def test_dry_run_does_not_create_the_database(proj):
    """A dry run must not open the project — opening it migrates and may rebuild the index."""
    root = proj.root
    proj.close()
    os.remove(os.path.join(root, ".research", "research.db"))
    r = UP.plan(root)
    assert r["project_schema"] is None
    assert not os.path.exists(os.path.join(root, ".research", "research.db"))
    assert any(s["kind"] == "schema" for s in r["steps"])


def test_dry_run_logs_no_event(legacy):
    root = legacy.root
    n = len(open(os.path.join(root, ".research", "events.jsonl"), encoding="utf-8").readlines())
    legacy.close()
    UP.plan(root)
    UP.status(root)
    assert len(open(os.path.join(root, ".research", "events.jsonl"), encoding="utf-8").readlines()) == n


def test_cli_dry_run_mutates_nothing(legacy):
    root = legacy.root
    legacy.close()
    before = _tree(root)
    r = subprocess.run([sys.executable, "-m", "research_cli", "upgrade", "--dry-run", "--json"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    json.loads(r.stdout)
    assert _tree(root) == before


# --------------------------------------------------------------------------- apply preserves everything

def test_upgrade_preserves_every_record(legacy):
    root = legacy.root
    before, events_before = _records(legacy), _events_only(legacy)
    legacy.close()
    res = UP.apply(root)
    assert res["ok"], res["failed"]
    with Project(root, sync=False) as p:
        assert _records(p) == before
        assert _events_only(p) >= events_before      # the audit log only ever grows
        assert any(e["action"] == "upgraded" for e in p.q("SELECT action FROM events"))


def test_upgrade_preserves_the_full_export_apart_from_its_own_audit_entry(legacy):
    """Everything the export says about the research itself is unchanged; only new events appear."""
    root = legacy.root
    before = FR.export(legacy)[1]
    legacy.close()
    UP.apply(root)
    with Project(root, sync=False) as p:
        after = FR.export(p)[1]
    # every section from the project definition up to the event history is byte-identical
    cut = "## 12. Audit / Event History"
    assert after.split(cut)[0].split("## 2.", 1)[1] == before.split(cut)[0].split("## 2.", 1)[1]


def test_upgrade_preserves_custom_config_and_unknown_keys(legacy):
    """A key missing from the file is inherited, not materialized — but the user's own keys survive.

    Under sparse configuration an absent key is not a defect, so the upgrade has nothing to add for it.
    What must hold is that the *effective* value is still right and nothing of the user's is lost.
    """
    root = legacy.root
    legacy.close()
    cfg_path = os.path.join(root, ".research", "config.yaml")
    raw = yamlio.load(open(cfg_path, encoding="utf-8").read())
    raw.pop("resume", None)                              # a default the sparse file need not carry
    open(cfg_path, "w", encoding="utf-8").write(yamlio.dump(raw, block=True))
    UP.apply(root)
    after = yamlio.load(open(cfg_path, encoding="utf-8").read())
    assert after["my_lab"] == {"cluster": "duke", "quota_gb": 500}   # unknown key kept
    assert after["project"]["goal"] == "my own goal"                  # custom value kept
    with Project(root, sync=False) as p:
        assert p.config["resume"] == {"stale_checkpoint_days": 14}    # inherited, though absent from the file
        assert p.config["my_lab"] == {"cluster": "duke", "quota_gb": 500}


def test_upgrade_offers_to_reformat_a_legacy_flow_style_config(legacy):
    """A flow-style file cannot be edited surgically and breaks on an inline comment, so it is reformatted."""
    root = legacy.root
    legacy.close()
    cfg_path = os.path.join(root, ".research", "config.yaml")
    raw = yamlio.load(open(cfg_path, encoding="utf-8").read())
    open(cfg_path, "w", encoding="utf-8").write(yamlio.dump(raw))          # flow style, as 0.2.0 wrote it
    before = Project(root, sync=False)
    effective_before = dict(before.config)
    before.close()
    assert any(s["kind"] == "config" and s["change"] == "reformat" for s in UP.plan(root)["steps"])
    UP.apply(root)
    text = open(cfg_path, encoding="utf-8").read()
    assert "project:\n" in text and "{name:" not in text                   # now block style
    with Project(root, sync=False) as after:
        assert after.config == effective_before                           # nothing changed in effect
    assert not any(s["kind"] == "config" for s in UP.plan(root)["steps"])  # and it converged


def test_upgrade_does_not_overwrite_a_customized_skill(legacy):
    root = legacy.root
    legacy.close()
    skill = os.path.join(root, ".research", "skills", "run-experiment", "SKILL.md")
    assert os.path.exists(skill)
    open(skill, "a", encoding="utf-8").write("\n\nMY LAB RULE: always log the seed.\n")
    mine = open(skill, encoding="utf-8").read()
    UP.apply(root)
    assert open(skill, encoding="utf-8").read() == mine


def test_upgrade_restores_a_missing_default_skill(legacy):
    root = legacy.root
    legacy.close()
    d = os.path.join(root, ".research", "skills", "run-experiment")
    shutil.rmtree(d)
    assert any(s["kind"] == "template" for s in UP.plan(root)["steps"])
    UP.apply(root)
    assert os.path.exists(os.path.join(d, "SKILL.md"))


def test_upgrade_creates_missing_directories(legacy):
    root = legacy.root
    legacy.close()
    for sub in ("notes", "reports"):
        shutil.rmtree(os.path.join(root, ".research", sub), ignore_errors=True)
    UP.apply(root)
    for sub in ("notes", "reports"):
        assert os.path.isdir(os.path.join(root, ".research", sub))


def test_upgrade_twice_is_a_no_op(legacy):
    root = legacy.root
    legacy.close()
    UP.apply(root)
    after_first = _tree(root)
    second = UP.apply(root)
    assert second["up_to_date"] or not second["applied"]
    changed = {k: v for k, v in _tree(root).items() if after_first.get(k) != v}
    assert changed == {}, changed


def test_upgrade_leaves_hand_edited_markdown_alone(legacy):
    root = legacy.root
    mirror = legacy.mirror_path("question", "Q-001")
    legacy.close()
    before = open(mirror, encoding="utf-8").read()
    assert "Edited by hand, with ünïcode." in before
    UP.apply(root)
    assert open(mirror, encoding="utf-8").read() == before


def test_rebuild_after_upgrade_succeeds_and_keeps_everything(legacy):
    root = legacy.root
    before = _records(legacy)
    legacy.close()
    UP.apply(root)
    with Project(root, sync=False) as p:
        p.rebuild_index(backup=False)
        assert _records(p) == before
        snap, text = FR.export(p)
        assert FR.coverage(snap, text)["ok"]


def test_full_export_coverage_still_passes_after_upgrade(legacy):
    root = legacy.root
    legacy.close()
    UP.apply(root)
    with Project(root, sync=False) as p:
        snap, text = FR.export(p)
        assert FR.coverage(snap, text)["ok"]


# --------------------------------------------------------------------------- schema versions

@pytest.mark.parametrize("version", [1, 2])
def test_old_schema_project_migrates_and_keeps_its_records(legacy, version):
    """Pretend the project was built by an older Research OS and upgrade it."""
    root = legacy.root
    before = _records(legacy)
    legacy.close()
    db = os.path.join(root, ".research", "research.db")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
    conn.commit()
    conn.close()
    pl = UP.plan(root)
    assert pl["project_schema"] == version
    assert any(s["kind"] == "schema" and s["change"] == "migrate" for s in pl["steps"])
    res = UP.apply(root)
    assert res["ok"], res["failed"]
    assert res["project_schema"] == UP.schema.SCHEMA_VERSION
    with Project(root, sync=False) as p:
        assert _records(p) == before


def test_newer_project_schema_is_refused_not_damaged(legacy):
    root = legacy.root
    legacy.close()
    before = _tree(root)
    db = os.path.join(root, ".research", "research.db")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'",
                 (str(UP.schema.SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()
    assert UP.plan(root)["downgrade_refused"]
    with pytest.raises(ResearchError):
        UP.apply(root)
    assert _tree(root) == before


def test_interrupted_upgrade_can_be_resumed(legacy, monkeypatch):
    """A step that blows up is reported, does not abort the others, and a re-run finishes the job."""
    root = legacy.root
    legacy.close()
    shutil.rmtree(os.path.join(root, ".research", "reports"), ignore_errors=True)
    shutil.rmtree(os.path.join(root, ".research", "skills", "run-experiment"))
    real = shutil.copy2
    monkeypatch.setattr(shutil, "copy2",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")) if "skills" in str(a[1])
                        else real(*a, **k))
    res = UP.apply(root)
    assert res["failed"] and not res["ok"]
    assert os.path.isdir(os.path.join(root, ".research", "reports"))  # the other steps still ran
    monkeypatch.undo()
    res2 = UP.apply(root)
    assert res2["ok"], res2["failed"]
    assert os.path.exists(os.path.join(root, ".research", "skills", "run-experiment", "SKILL.md"))


# --------------------------------------------------------------------------- AGENTS.md managed block

def test_preview_prints_the_block_without_touching_the_file(legacy):
    before = _tree(legacy.root)
    st = AM.state(legacy)
    assert st["new"] == AM.block()
    assert st["new"].startswith(AM.BEGIN) and st["new"].rstrip().endswith(AM.END)
    assert _tree(legacy.root) == before


def test_diff_is_empty_when_up_to_date_and_shows_only_the_block(legacy):
    assert AM.diff(legacy) == ""
    path = os.path.join(legacy.root, "AGENTS.md")
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace("### Core rules", "### Core rules EDITED"))
    d = AM.diff(legacy)
    assert "-### Core rules EDITED" in d and "+### Core rules" in d
    assert "House rules (mine)" not in d          # the user's prose is not part of the diff
    assert "More of my own notes" not in d


def test_apply_preserves_user_bytes_exactly(legacy):
    path = os.path.join(legacy.root, "AGENTS.md")
    outside_before = AM.outside_bytes(legacy)
    text = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(text.replace("## Research OS", "## Research OS (stale)"))
    r = AM.apply(legacy)
    assert r["changed"] and r["action"] == "replace"
    assert AM.outside_bytes(legacy) == outside_before
    now = open(path, encoding="utf-8").read()
    assert now.startswith(USER_PROSE) and now.endswith(USER_TAIL)
    assert "(stale)" not in now


def test_apply_is_idempotent(legacy):
    assert AM.apply(legacy)["changed"] is False
    before = open(os.path.join(legacy.root, "AGENTS.md"), "rb").read()
    AM.apply(legacy)
    assert open(os.path.join(legacy.root, "AGENTS.md"), "rb").read() == before


def test_missing_markers_add_a_block_without_disturbing_content(proj):
    path = os.path.join(proj.root, "AGENTS.md")
    open(path, "w", encoding="utf-8").write("# My instructions\n\nUse float64.\n")
    st = AM.state(proj)
    assert st["action"] == "add_block"
    AM.apply(proj)
    text = open(path, encoding="utf-8").read()
    assert text.startswith("# My instructions\n\nUse float64.\n")
    assert AM.BEGIN in text and AM.END in text


def test_no_agents_md_creates_one(proj):
    path = os.path.join(proj.root, "AGENTS.md")
    os.remove(path)
    assert AM.state(proj)["action"] == "create"
    AM.apply(proj)
    text = open(path, encoding="utf-8").read()
    assert text.startswith("# Agent instructions — ") and AM.BEGIN in text


@pytest.mark.parametrize("broken,why", [
    ("{block}\n{block}", "duplicated"),
    ("<!-- research:end -->\nstuff\n<!-- research:begin x -->", "reversed"),
    ("{block}\n<!-- research:end -->", "extra end"),
])
def test_malformed_markers_refuse_and_change_nothing(proj, broken, why):
    path = os.path.join(proj.root, "AGENTS.md")
    open(path, "w", encoding="utf-8").write("mine\n\n" + broken.format(block=AM.block()) + "\n")
    before = open(path, "rb").read()
    st = AM.state(proj)
    assert st["action"] == "refuse" and st["problem"], why
    for fn in (AM.apply, AM.diff, AM.render):
        with pytest.raises(ResearchError):
            fn(proj)
    assert open(path, "rb").read() == before


def test_upgrade_reports_a_malformed_block_but_still_upgrades_the_rest(legacy):
    root = legacy.root
    legacy.close()
    path = os.path.join(root, "AGENTS.md")
    open(path, "a", encoding="utf-8").write("\n<!-- research:begin dup -->\nx\n<!-- research:end -->\n")
    before = open(path, "rb").read()
    shutil.rmtree(os.path.join(root, ".research", "reports"), ignore_errors=True)
    res = UP.apply(root)
    assert any(s["change"] == "refuse" and s["kind"] == "agents_md" for s in res["refused"])
    assert os.path.isdir(os.path.join(root, ".research", "reports"))
    assert open(path, "rb").read() == before


def test_crlf_line_endings_outside_the_block_survive(proj):
    path = os.path.join(proj.root, "AGENTS.md")
    text = open(path, encoding="utf-8", newline="").read()
    start = text.index(AM.BEGIN)
    head = "# Mine\r\n\r\nUse float64.\r\n\r\n"
    open(path, "w", encoding="utf-8", newline="").write(head + text[start:])
    AM.apply(proj)
    assert open(path, encoding="utf-8", newline="").read().startswith(head)


def test_init_on_an_existing_project_is_safe(legacy):
    """`research init` on an initialized project must not reset anything."""
    root = legacy.root
    before = _records(legacy)
    goal = legacy.config["project"]["goal"]
    agents = open(os.path.join(root, "AGENTS.md"), "rb").read()
    legacy.close()
    p2 = Project.init(root)
    try:
        assert _records(p2) == before
        assert p2.config["project"]["goal"] == goal
        assert p2.config["my_lab"] == {"cluster": "duke", "quota_gb": 500}
        assert open(os.path.join(root, "AGENTS.md"), "rb").read() == agents
    finally:
        p2.close()


def test_init_does_not_fail_on_a_project_with_broken_markers(legacy):
    root = legacy.root
    legacy.close()
    path = os.path.join(root, "AGENTS.md")
    open(path, "a", encoding="utf-8").write("\n<!-- research:begin dup -->\nx\n<!-- research:end -->\n")
    before = open(path, "rb").read()
    p2 = Project.init(root)
    p2.close()
    assert open(path, "rb").read() == before


# --------------------------------------------------------------------------- CLI

def _cli(cwd, *args):
    return subprocess.run([sys.executable, "-m", "research_cli", *args], cwd=cwd,
                          capture_output=True, text=True)


def test_cli_surface(legacy):
    root = legacy.root
    legacy.close()
    assert _cli(root, "upgrade", "--status", "--json").returncode == 0
    r = _cli(root, "agents-md", "--preview")
    assert r.returncode == 0 and r.stdout.startswith(AM.BEGIN)
    assert _cli(root, "agents-md", "--diff").returncode == 0
    assert _cli(root, "upgrade", "--apply", "--json").returncode == 0
    assert json.loads(_cli(root, "upgrade", "--status", "--json").stdout)["up_to_date"]


def test_cli_upgrade_refuses_a_newer_project(legacy):
    root = legacy.root
    legacy.close()
    db = os.path.join(root, ".research", "research.db")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(UP.schema.SCHEMA_VERSION + 5),))
    conn.commit()
    conn.close()
    r = _cli(root, "upgrade", "--dry-run")
    assert r.returncode == 1 and "newer" in r.stdout.lower() + r.stderr.lower()


def test_marker_detection_is_line_anchored(proj):
    """Prose that merely mentions a marker must not be mistaken for one.

    This is a real trap: the managed block itself, and a user's own notes about it, can both contain the
    marker text inline. A substring match would see several markers and refuse to touch the file (or,
    worse, cut it in the wrong place).
    """
    path = os.path.join(proj.root, "AGENTS.md")
    text = open(path, encoding="utf-8").read()
    with_prose = text.replace(
        "# Agent instructions",
        "Note to self: the block between `<!-- research:begin … -->` and `<!-- research:end -->` is managed.\n\n"
        "# Agent instructions")
    open(path, "w", encoding="utf-8").write(with_prose)
    st = AM.state(proj)
    assert st["problem"] is None and st["action"] in ("none", "replace")
    AM.apply(proj)
    assert "Note to self:" in open(path, encoding="utf-8").read()


def test_the_managed_block_does_not_contain_a_bare_marker(proj):
    """The block's own text must never contain a line that looks like a marker."""
    body = AM.block().split("\n")
    inner = body[1:-1]          # everything between the real markers
    for line in inner:
        assert not line.lstrip().startswith(AM.BEGIN), line
        assert not line.lstrip().startswith(AM.END), line


def test_upgrade_appends_missing_gitignore_lines_without_rewriting(legacy):
    """Generated output must not land in version control; the user's own lines are untouched."""
    root = legacy.root
    legacy.close()
    gi = os.path.join(root, ".research", ".gitignore")
    # as an older Research OS wrote it: no reports/ or hpc/ lines, plus the user's own rule
    with open(gi, "w", encoding="utf-8") as f:
        f.write("research.db\ncache/\nbin/\n# my own rule\nscratch/\n")
    mine = open(gi, encoding="utf-8").read()
    assert any(s["kind"] == "gitignore" for s in UP.plan(root)["steps"])
    UP.apply(root)
    now = open(gi, encoding="utf-8").read()
    assert now.startswith(mine)                     # nothing rewritten or reordered
    assert "reports/" in now and "hpc/bundles/" in now
    assert "# my own rule" in now and "scratch/" in now
    UP.apply(root)
    assert open(gi, encoding="utf-8").read() == now  # idempotent


def test_upgrade_does_not_touch_a_gitignore_that_already_covers_it(legacy):
    root = legacy.root
    legacy.close()
    gi = os.path.join(root, ".research", ".gitignore")
    with open(gi, "a", encoding="utf-8") as f:
        f.write("reports/\nhpc/bundles/\nhpc/incoming/\n")
    before = open(gi, "rb").read()
    assert not any(s["kind"] == "gitignore" for s in UP.plan(root)["steps"])
    UP.apply(root)
    assert open(gi, "rb").read() == before


def test_upgrade_refreshes_an_unmodified_first_party_skill(legacy):
    """A shipped skill's guidance changes between versions; an untouched copy should get the new text."""
    root = legacy.root
    legacy.close()
    skill = os.path.join(root, ".research", "skills", "run-experiment", "SKILL.md")
    # simulate an older version's copy, recorded as ours and unmodified by the user
    with open(skill, "w", encoding="utf-8") as f:
        f.write("---\nname: run-experiment\ndescription: old\n---\n\nold guidance\n")
    from research.util import sha256_bytes
    UP._record_template_hash(root, os.path.join("skills", "run-experiment", "SKILL.md"),
                             sha256_bytes(open(skill, "rb").read()))
    assert any(s["change"] == "refresh" for s in UP.plan(root)["steps"])
    UP.apply(root)
    now = open(skill, encoding="utf-8").read()
    assert "old guidance" not in now
    assert "Register, execute and track" in now          # the shipped text
    assert not any(s["change"] == "refresh" for s in UP.plan(root)["steps"])   # converged


def test_upgrade_never_overwrites_a_skill_the_user_edited(legacy):
    """The whole point of the hash record: an edited file is left alone, not silently replaced."""
    root = legacy.root
    legacy.close()
    skill = os.path.join(root, ".research", "skills", "run-experiment", "SKILL.md")
    from research.util import sha256_bytes
    UP._record_template_hash(root, os.path.join("skills", "run-experiment", "SKILL.md"),
                             sha256_bytes(open(skill, "rb").read()))
    with open(skill, "a", encoding="utf-8") as f:
        f.write("\n\nMY LAB RULE: always log the seed.\n")
    mine = open(skill, encoding="utf-8").read()
    UP.apply(root)
    assert open(skill, encoding="utf-8").read() == mine


def test_upgrade_leaves_an_unrecorded_skill_alone(legacy):
    """Without a recorded hash we cannot tell a customization from a stale copy, so we do not touch it."""
    root = legacy.root
    legacy.close()
    skill = os.path.join(root, ".research", "skills", "run-experiment", "SKILL.md")
    with open(skill, "w", encoding="utf-8") as f:
        f.write("unknown provenance\n")
    UP.apply(root)
    assert open(skill, encoding="utf-8").read() == "unknown provenance\n"
