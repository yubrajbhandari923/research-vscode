"""YAML bootstrap: validated, previewed, applied through the same initializer as the wizard, reproducible."""
import os

import pytest

from research import bootstrap as BS, initproject as IP, yamlio
from research.store import Project
from research.util import ResearchError


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def doc(**over):
    d = yamlio.load(BS.TEMPLATE)
    d["project"]["name"] = "demo"
    d["guide"]["enabled"] = False
    d["tools"].update(link_skills=False, starter_hooks=False)
    for k, v in over.items():
        d[k] = {**d.get(k, {}), **v} if isinstance(v, dict) else v
    return d


def test_template_is_valid_and_previews_without_writing(tmp_path):
    r = BS.check(BS.TEMPLATE.replace("my-project", "demo"), str(tmp_path / "p"))
    assert r["ok"], r["errors"]
    assert any("experiments" in t for t in r["plan"]["tree"]) and not (tmp_path / "p").exists()


@pytest.mark.parametrize("edit,needle", [
    ({"governance": {"tasks": {"agent_creation": "anything"}}}, "governance.tasks.agent_creation"),
    ({"supervisor": {"max_parallel_agents": 0}}, "supervisor.max_parallel_agents"),
    ({"bogus": {"a": 1}}, "bogus: unknown section"),
    ({"layout": {"nope": "x"}}, "layout.nope: unknown setting"),
    ({"schema_version": 7}, "schema_version"),
])
def test_invalid_settings_name_their_path(tmp_path, edit, needle):
    r = BS.check(yamlio.dump(doc(**edit), block=True), str(tmp_path / "p"))
    assert not r["ok"] and any(needle in e for e in r["errors"]), r["errors"]


def test_yaml_and_wizard_produce_the_same_project_and_export_round_trips(tmp_path):
    d = doc(governance={"tasks": {"max_auto_per_experiment": 2}}, supervisor={"max_total_attempts": 7})
    text = yamlio.dump(d, block=True)
    ry, rw = tmp_path / "yaml", tmp_path / "wiz"
    ry.mkdir(), rw.mkdir()
    BS.apply(text, str(ry))
    spec, errs = BS.to_spec(d)
    assert not errs
    IP.apply(str(rw), spec)                                           # the wizard path: the same spec goes in
    cy, cw = (Project(str(r / "main")) for r in (ry, rw))
    assert cy.config["governance"]["tasks"]["max_auto_per_experiment"] == 2 == cw.config["governance"]["tasks"]["max_auto_per_experiment"]
    assert cy.config["supervisor"]["max_total_attempts"] == 7
    assert sorted(os.listdir(ry)) == sorted(os.listdir(rw))
    out = BS.export(cy)
    assert "ssh_host" not in out and str(tmp_path) not in out        # nothing machine-specific leaves the project
    again = yamlio.load(out)
    assert again["governance"]["tasks"]["max_auto_per_experiment"] == 2 and again["supervisor"]["max_total_attempts"] == 7
    r2 = BS.check(out, str(tmp_path / "other-machine"))          # reproducible elsewhere
    assert r2["ok"], r2["errors"]
    cy.close(), cw.close()


def test_apply_refuses_an_existing_project_and_invalid_files(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    BS.apply(yamlio.dump(doc(), block=True), str(root))
    with pytest.raises(ResearchError):
        BS.apply(yamlio.dump(doc(), block=True), str(root))             # never overwrites records or history
    with pytest.raises(ResearchError):
        BS.apply("schema_version: 1\nproject: [", str(tmp_path / "q"))
