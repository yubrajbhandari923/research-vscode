"""Project format 2: explicit identification, and v1 projects are rejected without being touched."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from research.store import Project
from research.util import IncompatibleProject


def tree(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def v1_project(root):
    os.makedirs(os.path.join(root, ".research", "questions"))
    open(os.path.join(root, ".research", "config.yaml"), "w").write("project:\n  name: old\n")
    open(os.path.join(root, ".research", "questions", "Q-001.md"), "w").write("---\nid: Q-001\n---\nold record\n")


def cli(cwd, *a):
    return subprocess.run([sys.executable, "-m", "research_cli", *a], cwd=cwd, capture_output=True, text=True)


def test_new_projects_are_identified_as_format_2(proj):
    assert json.load(open(proj.path("format.json")))["format"] == 2


def test_v1_project_is_rejected_with_instructions_and_left_byte_identical(repo):
    v1_project(str(repo))
    before = tree(str(repo / ".research"))
    with pytest.raises(IncompatibleProject) as e:
        Project(str(repo))
    assert "v1" in str(e.value) and ".research-v1-archive" in e.value.hint and "research init" in e.value.hint
    assert tree(str(repo / ".research")) == before, "nothing was created, converted or deleted"


def test_cli_rejects_v1_everywhere_including_init(repo):
    v1_project(str(repo))
    before = tree(str(repo / ".research"))
    for args in (("current",), ("experiment", "list"), ("init",)):
        r = cli(repo, *args)
        assert r.returncode == 2 and "v1 project" in r.stderr, (args, r.stderr)
    assert tree(str(repo / ".research")) == before


def test_other_formats_are_rejected_too(proj, repo):
    proj.close()
    json.dump({"format": 3}, open(repo / ".research" / "format.json", "w"))
    with pytest.raises(IncompatibleProject, match="format-3"):
        Project(str(repo))


def test_valid_v2_projects_still_rebuild_from_their_records(proj, repo):
    from research import services
    services.create_question(proj, "survives rebuild")
    proj.rebuild_index(backup=False)
    assert proj.list("question")[0]["title"] == "survives rebuild"
