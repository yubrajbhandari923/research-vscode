"""Guide review (deterministic vs independent model review vs human approval) and the scientific-briefing skill."""
import os
import shutil

import pytest

from research import agents, guide as G
from research import guide_quarto as GQ
from research import guide_review as GR
from research import provenance as PV
from research import services as S
from research import skills as SK
from research import verification as V
from research.util import NotFound, PolicyBlocked, ResearchError
from test_research_os import as_agent, as_human
from test_guide_quarto import write_story_chapter
import guide_story as GS
from test_upgrade import legacy  # noqa: F401  (fixture)

CH = "investigations/sensitivity.qmd"


def drop_skill(proj):
    """New projects ship the skill; remove it to test the missing case."""
    assert os.path.isdir(proj.path("skills", "scientific-briefing"))
    shutil.rmtree(proj.path("skills", "scientific-briefing"))


@pytest.fixture
def story(proj, monkeypatch):
    s = GS.build(proj, monkeypatch)
    write_story_chapter(proj, s)
    return s


def test_unreviewed_by_default_and_review_is_not_approval(proj, story):
    assert GR.status(proj)[CH]["label"] == "unreviewed"
    r = GR.record(proj, CH, "sound", "reviewer-model", {"evidence_fidelity": "ok"}, "Numbers checked.")
    st = GR.status(proj)[CH]
    assert r["verdict"] == "sound" and st["model_review"]["current"] and st["human_approval"] is None
    assert st["label"] == "reviewed, awaiting approval"  # never "approved" from a model review alone


def test_human_approval_is_separate_and_bound_to_the_text(proj, story):
    GR.record(proj, CH, "sound", "m")
    GR.approve(proj, CH, "Dr. A")
    assert GR.status(proj)[CH]["label"] == "approved"
    path = os.path.join(PV.guide_dir(proj), CH)
    open(path, "a").write("\nA new unreviewed sentence.\n")
    st = GR.status(proj)[CH]
    assert st["label"] == "review outdated" and not st["human_approval"]["current"] and not st["model_review"]["current"]


def test_review_goes_outdated_when_cited_evidence_changes(proj, story, monkeypatch):
    GR.record(proj, CH, "sound", "m")
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f2"], "contradicted", "Rechecked: the ratio does not hold.")
    as_human(proj)
    st = GR.status(proj)[CH]
    assert not st["model_review"]["current"] and st["deterministic"]["errors"] >= 1


def test_cannot_approve_over_provenance_errors_without_recorded_override(proj, story, monkeypatch):
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f2"], "contradicted", "Does not hold.")
    as_human(proj)
    with pytest.raises(PolicyBlocked):
        GR.approve(proj, CH, "Dr. A")
    e = GR.approve(proj, CH, "Dr. A", force=True)
    assert e["overrode"]  # the override is on the record


def test_unsound_review_blocks_approval(proj, story):
    GR.record(proj, CH, "unsound", "m", {"scientific_validity": "fail"}, concerns=["conclusion exceeds the data"])
    with pytest.raises(PolicyBlocked):
        GR.approve(proj, CH, "Dr. A")


def test_review_validation(proj, story):
    with pytest.raises(ResearchError):
        GR.record(proj, CH, "great", "m")
    with pytest.raises(ResearchError):
        GR.record(proj, CH, "sound", "m", {"uncertainty": "concern"})  # sound cannot carry a concern
    with pytest.raises(ResearchError):
        GR.record(proj, CH, "concerns", "m", {"made_up": "ok"})
    with pytest.raises(NotFound):
        GR.record(proj, "nope.qmd", "sound", "m")


def test_review_changes_no_record_and_history_is_appended(proj, story):
    before = {k: v for k, v in PV.load_claims(proj).items()}
    GR.record(proj, CH, "concerns", "m1")
    GR.record(proj, CH, "sound", "m2")
    assert PV.load_claims(proj) == before
    assert len(PV._load(proj, "reviews.json", "reviews")[CH]["model"]) == 2


def test_review_request_requires_and_injects_the_briefing_skill(proj, story):
    drop_skill(proj)
    with pytest.raises(PolicyBlocked):
        GR.request(proj, CH)  # skill not installed
    S.create_skill(proj, "scientific-briefing", description="x", body="RULE-BRIEF: cite only examined sources.")
    d = GR.request(proj, CH)
    assert d["role"] == "verifier" and [r["name"] for r in d["skills"]["required"]] == ["scientific-briefing"]
    text = open(proj.abspath(d["brief"]), encoding="utf-8").read()
    assert "RULE-BRIEF" in text and "evidence_fidelity" in text and "not approval" in text.lower()


def test_guide_refresh_requires_briefing_skill_only_for_quarto_guides(proj, story, monkeypatch):
    proj.config.setdefault("guide", {})["model"] = "opus"
    # v1.0-style project (no provenance files) is unaffected
    assert agents.dispatch(proj, "analyst", None, "x")["skills"]["required"] == []
    d0 = G.refresh(proj, model="opus")  # a fresh project ships the skill: it is injected
    assert [r["name"] for r in d0["skills"]["required"]] == ["scientific-briefing"]
    drop_skill(proj)
    with pytest.raises(PolicyBlocked):
        G.refresh(proj, model="opus")  # Quarto guide adopted but the skill is missing: blocked
    S.create_skill(proj, "scientific-briefing", description="x", body="BRIEF-RULE")
    d = G.refresh(proj, model="opus")
    assert [r["name"] for r in d["skills"]["required"]] == ["scientific-briefing"]
    assert "BRIEF-RULE" in open(proj.abspath(d["brief"]), encoding="utf-8").read()


def test_shipped_briefing_skill_is_installed_and_names_the_review_commands(proj):
    from research.store import TEMPLATES
    md = (TEMPLATES / "skills" / "scientific-briefing" / "SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("---\nname: scientific-briefing")
    for needle in ("research guide validate", "research guide claim add", "research guide review", "{#clm-", "retracted"):
        assert needle in md


def test_v1_0_project_upgrades_non_destructively_and_quarto_guide_stays_opt_in(legacy):
    from research import upgrade as UP
    from test_upgrade import _records, _tree
    shutil.rmtree(legacy.path("skills", "scientific-briefing"))  # what a v1.0 project looks like
    gdir = os.path.join(legacy.root, "docs")
    os.makedirs(gdir, exist_ok=True)
    mine = os.path.join(gdir, "SCIENTIST_GUIDE.md")
    open(mine, "w").write("# My guide\n\nHand-written.\n")
    records, root = _records(legacy), legacy.root
    legacy.close()
    pl = UP.plan(root)
    assert any(x["path"].endswith("skills/scientific-briefing/SKILL.md") and x["change"] == "add" for x in pl["steps"])
    assert pl["guide"]["legacy_guide"] == "docs/SCIENTIST_GUIDE.md" and pl["guide"]["quarto_guide"] is False
    assert not os.path.exists(os.path.join(root, "docs", "scientist-guide"))  # planning and applying never create it
    before = _tree(os.path.join(root, ".research", "experiments"))
    UP.apply(root)
    assert open(mine).read() == "# My guide\n\nHand-written.\n"
    assert not os.path.exists(os.path.join(root, "docs", "scientist-guide"))
    assert _tree(os.path.join(root, ".research", "experiments")) == before
    assert os.path.exists(os.path.join(root, ".research", "skills", "scientific-briefing", "SKILL.md"))
    from research.store import Project
    with Project(root, sync=False) as p2:
        assert _records(p2) == records
    assert UP.plan(root)["up_to_date"]  # idempotent


def test_rpc_and_mcp_guide_views_are_read_only(proj, story, repo):
    from research.mcp import _tools
    from test_plans_tasks_interfaces import rpc
    GR.record(proj, CH, "sound", "m")
    events = proj.q("SELECT COUNT(*) AS n FROM events")[0]["n"]
    claims = dict(PV.load_claims(proj))
    proj.close()
    st = rpc(repo, ("guide_status", {}))[1]["result"]
    assert st["provenance"]["sections"][CH]["state"] in ("fresh", "stale") and st["review"][CH]["label"] == "reviewed, awaiting approval"
    assert st["quarto"]["ok"] in (True, False)
    v = rpc(repo, ("guide_validate", {}))[1]["result"]
    assert v["ok"] and "neither" in v["note"]
    ro = {t.name for t in _tools() if not t.mutating}
    assert {"guide_status", "guide_validate"} <= ro
    from research.store import Project
    with Project(str(repo), sync=False) as p2:
        assert p2.q("SELECT COUNT(*) AS n FROM events")[0]["n"] == events and PV.load_claims(p2) == claims


def test_proposals_for_human_owned_sections_are_listed_and_not_rendered(proj, story):
    PV.section_set(proj, CH, owner="human")
    os.makedirs(os.path.join(PV.guide_dir(proj), "_proposals", "investigations"))
    open(os.path.join(PV.guide_dir(proj), "_proposals", "investigations", "sensitivity.md"), "w").write("Suggested rewrite.\n")
    st = G.status(proj)["provenance"]
    assert st["proposals"] == ["_proposals/investigations/sensitivity.md"]
    assert st["sections"][CH]["owner"] == "human"
    assert PV.validate(proj)["ok"]  # a proposal file is not a chapter and is not validated as one
