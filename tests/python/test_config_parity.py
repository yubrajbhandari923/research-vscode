"""Files are authoritative: UI/RPC edits and manual file edits round-trip, conflicts are caught, errors never overwrite."""
import json
import os

import pytest

from research import registry as R
from research.store import Project
from research.util import ResearchError

KEY = "agent_policy.max_runs_without_synthesis"


def opt(p, key):
    return next(o for o in R.describe(p)["options"] if o["key"] == key)


def test_ui_edit_writes_a_minimal_commented_diff_and_manual_edit_is_seen(proj, repo):
    cfg = repo / ".research" / "config.yaml"
    cfg.write_text(cfg.read_text() + "\n# my note: keep this comment\nmy_custom_key: 42\n")
    r = R.apply(proj, {KEY: 9})
    assert r["changed"] and "my_custom_key: 42" in cfg.read_text() and "# my note: keep this comment" in cfg.read_text()
    assert [l for l in r["diff"].splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---")], "diff must be reviewable"
    assert opt(proj, KEY)["value"] == 9 and opt(proj, KEY)["file"].endswith("config.yaml")
    # a manual edit while the app is closed shows up in a fresh Project, with no sync step
    cfg.write_text(cfg.read_text().replace("max_runs_without_synthesis: 9", "max_runs_without_synthesis: 4"))
    q = Project.find(str(repo))
    try:
        assert opt(q, KEY)["value"] == 4 and q.config["agent_policy"]["max_runs_without_synthesis"] == 4
    finally:
        q.close()


def test_concurrent_external_edit_is_detected_and_nothing_is_written(proj, repo):
    cfg = repo / ".research" / "config.yaml"
    digest = R.describe(proj)["digests"]["project"]
    cfg.write_text(cfg.read_text() + "\n# edited elsewhere\n")
    before = cfg.read_text()
    with pytest.raises(ResearchError, match="changed on disk"):
        R.apply(proj, {KEY: 8}, expected=digest)
    with pytest.raises(ResearchError, match="changed on disk"):
        R.preview(proj, {KEY: 8}, expected=digest)
    assert cfg.read_text() == before
    assert R.apply(proj, {KEY: 8}, expected=R.describe(proj)["digests"]["project"])["changed"]


def test_invalid_manual_edit_is_diagnosed_not_overwritten(proj, repo):
    cfg = repo / ".research" / "config.yaml"
    cfg.write_text(cfg.read_text() + "\nagent_policy:\n  max_runs_without_synthesis: lots\n  typo_key: 1\n")
    before = cfg.read_text()
    d = opt(Project.find(str(repo)), KEY)
    assert d["errors"], "invalid value must be reported, not silently replaced"
    with pytest.raises(ResearchError):
        R.apply(proj, {"agent_policy.max_failed_runs_without_review": 0})
    assert cfg.read_text() == before


def test_precedence_default_machine_project_is_visible(proj, repo, tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    (xdg / "research-os").mkdir(parents=True)
    (xdg / "research-os" / "config.yaml").write_text("agent_policy:\n  max_runs_without_synthesis: 6\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    q = Project.find(str(repo))
    try:
        o = opt(q, KEY)
        assert (o["value"], o["source"]) == (6, "machine") and o["file"].endswith("config.yaml")
        R.apply(q, {KEY: 3})
        o = opt(q, KEY)
        assert (o["value"], o["source"]) == (3, "project") and [x for x in o["overridden_in"]]
    finally:
        q.close()


def test_rpc_cli_and_ui_agree_on_the_effective_value(proj, repo):
    import subprocess, sys
    R.apply(proj, {KEY: 7})
    out = subprocess.run([sys.executable, "-m", "research_cli", "config", "show", "--effective", "--json"], cwd=repo,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    eff = json.loads(out.stdout)
    eff = eff.get("settings", eff)
    assert eff["agent_policy"]["max_runs_without_synthesis"] == 7 == opt(Project.find(str(repo)), KEY)["value"]
    assert "max_runs_without_synthesis: 7" in (repo / ".research" / "context" / "policy.md").read_text().replace("**", "") or "7" in (repo / ".research" / "context" / "policy.md").read_text()


def test_machine_override_does_not_make_the_generated_reference_block_look_edited(proj, repo, tmp_path, monkeypatch):
    xdg = tmp_path / "xdg2"
    (xdg / "research-os").mkdir(parents=True)
    (xdg / "research-os" / "config.yaml").write_text("hpc:\n  default_cluster: duke\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    from research import configfile
    from research.store import DEFAULT_CONFIG
    q = Project.find(str(repo))
    try:
        r = configfile.check(q.raw_config, DEFAULT_CONFIG, (repo / ".research" / "config.yaml").read_text())
        assert r["edited_comments"] == []
    finally:
        q.close()
