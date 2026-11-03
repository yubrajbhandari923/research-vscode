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


def test_guide_draft_requires_the_briefing_skill(proj, story, monkeypatch):
    proj.config.setdefault("guide", {})["model"] = "opus"
    GQ.init_quarto(proj)
    assert agents.dispatch(proj, "analyst", None, "x")["skills"]["required"] == []  # an ordinary dispatch is unaffected
    d0 = G.draft(proj, model="opus")  # a fresh project ships the skill: it is injected
    assert [r["name"] for r in d0["skills"]["required"]] == ["scientific-briefing"]
    drop_skill(proj)
    with pytest.raises(PolicyBlocked):
        G.draft(proj, model="opus")  # the skill is missing: blocked
    S.create_skill(proj, "scientific-briefing", description="x", body="BRIEF-RULE")
    d = G.draft(proj, model="opus")
    assert [r["name"] for r in d["skills"]["required"]] == ["scientific-briefing"]
    assert "BRIEF-RULE" in open(proj.abspath(d["brief"]), encoding="utf-8").read()


def test_shipped_briefing_skill_is_installed_and_names_the_review_commands(proj):
    from research.store import TEMPLATES
    md = (TEMPLATES / "skills" / "scientific-briefing" / "SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("---\nname: scientific-briefing")
    for needle in ("research guide validate", "research guide claim add", "research guide review", "{#clm-", "retracted"):
        assert needle in md




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


def test_researcher_panels_show_review_and_approval_state_separately(proj, story):
    GQ.prepare(proj)
    panel = open(os.path.join(PV.guide_dir(proj), "_generated", "evidence", GQ.slug_of(CH) + ".md"), encoding="utf-8").read()
    assert "Independent model review:** none" in panel and "human approval:** none" in panel
    GR.record(proj, CH, "concerns", "reviewer-x", {"uncertainty": "concern"})
    GQ.prepare(proj)
    panel = open(os.path.join(PV.guide_dir(proj), "_generated", "evidence", GQ.slug_of(CH) + ".md"), encoding="utf-8").read()
    assert "concerns by reviewer-x" in panel and "current" in panel and "human approval:** none" in panel
    open(os.path.join(PV.guide_dir(proj), CH), "a").write("\nMore text.\n")
    GQ.prepare(proj)
    panel = open(os.path.join(PV.guide_dir(proj), "_generated", "evidence", GQ.slug_of(CH) + ".md"), encoding="utf-8").read()
    assert "OUTDATED" in panel
    status = open(os.path.join(PV.guide_dir(proj), "_generated", "evidence-status.qmd"), encoding="utf-8").read()
    assert "review outdated" in status
