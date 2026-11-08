"""Skills: import, link, match, remove."""
import pytest
from research import context as C
from research import plans as P
from research import skills as SK
from research.util import ResearchError
from conftest import git


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
