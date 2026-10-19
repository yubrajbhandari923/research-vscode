import json
import os

import pytest

from research import provenance as PV
from research import services as S
from research import verification as V
from research import runs as R
from research.util import NotFound, ResearchError
from test_research_os import as_agent, as_human
import guide_story as GS


@pytest.fixture
def story(proj, monkeypatch):
    return GS.build(proj, monkeypatch)


def codes(rep):
    return {i["code"] for i in rep["issues"]}


def mk(proj, story, **kw):
    ch = GS.write_chapter(proj)
    return PV.claim_add(proj, kw.pop("statement", "Structured perturbations change the data weakly."),
                        kw.pop("kind", "observation"), ch, kw.pop("sources", [story["f2"], f"{story['r2']}#sensitivity_ratio"]), **kw)


def test_claim_resolves_to_valid_evidence_and_is_traceable(proj, story):
    c = mk(proj, story)
    assert c["id"] == "CLM-0001" and c["sources"][1]["basis"]["value"] == 0.04
    rep = PV.validate(proj)
    assert rep["ok"] and rep["claims"]["CLM-0001"]["status"] == "traceable"


def test_registration_refuses_missing_record_and_bad_kind(proj, story):
    with pytest.raises(NotFound):
        PV.claim_add(proj, "x", "observation", "a.qmd", ["F-999"])
    with pytest.raises(ResearchError):
        PV.claim_add(proj, "x", "fact", "a.qmd", [story["f1"]])
    with pytest.raises(ResearchError):
        PV.claim_add(proj, "x", "observation", "a.qmd", ["nonsense"])
    assert PV.load_claims(proj) == {}


def test_ids_are_stable_and_independent_of_experiments(proj, story):
    a = mk(proj, story)
    PV.claim_update(proj, a["id"], status="retired")
    b = mk(proj, story, statement="another")
    assert (a["id"], b["id"]) == ("CLM-0001", "CLM-0002")  # a retired id is never reused


def test_unsupported_evidential_claim_is_an_error_but_speculation_is_not(proj, story):
    ch = GS.write_chapter(proj)
    PV.claim_add(proj, "It is probably due to X.", "interpretation", ch, [])
    PV.claim_add(proj, "Maybe the ratio is the same elsewhere.", "speculation", ch, [])
    rep = PV.validate(proj)
    assert rep["claims"]["CLM-0001"]["status"] == "unsupported" and rep["claims"]["CLM-0002"]["status"] == "speculation"
    assert not rep["ok"]


def test_observation_must_cite_data_not_only_a_document(proj, story):
    open(os.path.join(proj.root, "notes.md"), "w").write("theory")
    ch = GS.write_chapter(proj)
    PV.claim_add(proj, "Measured thing", "observation", ch, ["doc:notes.md"])
    assert "observation_without_data" in codes(PV.validate(proj)["claims"]["CLM-0001"])


def test_changed_metric_makes_claim_stale_and_section_stale(proj, story):
    mk(proj, story)
    PV.pin_section(proj, "investigations/sensitivity.qmd")
    assert PV.validate(proj)["sections"]["investigations/sensitivity.qmd"]["state"] == "fresh"
    R.log_metric(proj, "sensitivity_ratio", 0.07, run=story["r2"])  # a second, different value at the same step
    rep = PV.validate(proj)
    cl = rep["claims"]["CLM-0001"]
    assert cl["status"] == "invalid" and "metric_unverifiable" in codes(cl)  # two different values now recorded: ambiguous
    assert rep["sections"]["investigations/sensitivity.qmd"]["state"] in ("stale", "invalid")


def test_contradicted_finding_flags_dependent_claim_only(proj, story, monkeypatch):
    mk(proj, story)
    ch = GS.write_chapter(proj, "investigations/baseline.qmd", "# B\n\nUniform [x]{#clm-0002 .claim}.\n")
    PV.claim_add(proj, "Uniform changes are recovered.", "observation", ch, [story["f1"]])
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f1"], "contradicted", "Leakage found; RMSE not valid.")
    as_human(proj)
    rep = PV.validate(proj)
    assert rep["claims"]["CLM-0002"]["status"] == "contradicted" and "contradicted_support" in codes(rep["claims"]["CLM-0002"])
    assert rep["claims"]["CLM-0001"]["status"] == "traceable"  # unrelated evidence is untouched
    assert rep["sections"]["investigations/baseline.qmd"]["state"] == "invalid"
    assert rep["sections"]["investigations/sensitivity.qmd"]["state"] != "invalid"


def test_historical_claim_may_cite_a_retracted_finding(proj, story, monkeypatch):
    ch = GS.write_chapter(proj)
    PV.claim_add(proj, "Initially we believed uniform changes were recovered; this was later retracted.", "interpretation",
                 ch, [story["f1"]], historical=True)
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f1"], "contradicted", "Leakage.")
    as_human(proj)
    rep = PV.validate(proj)
    assert rep["ok"] and rep["claims"]["CLM-0001"]["status"] == "historical"


def test_superseded_finding_is_flagged_and_points_to_replacement(proj, story):
    ch = GS.write_chapter(proj)
    PV.claim_add(proj, "Uniform recovered", "observation", ch, [story["f1"]])
    new = S.create_finding(proj, "Better uniform result", statement="v2", supports=[story["r1"]])["id"]
    S.supersede_finding(proj, story["f1"], by=new, reason="corrected")
    i = [x for x in PV.validate(proj)["claims"]["CLM-0001"]["issues"] if x["code"] == "superseded_support"][0]
    assert new in i["message"]


def test_unreviewed_evidence_is_a_warning_label_preliminary(proj, story):
    ch = GS.write_chapter(proj)
    f = S.create_finding(proj, "New", statement="n", supports=[story["r1"]])["id"]
    PV.claim_add(proj, "A new result", "observation", ch, [f])
    rep = PV.validate(proj)
    assert rep["ok"] and rep["claims"]["CLM-0001"]["status"] == "preliminary"


def test_failed_run_cannot_support(proj, story):
    ch = GS.write_chapter(proj)
    bad = R.create_run(proj, story["e1"], command="sim", status="failed")["id"]
    PV.claim_add(proj, "from failed", "observation", ch, [bad])
    assert "run_not_completed" in codes(PV.validate(proj)["claims"]["CLM-0001"])


def test_review_after_pin_changes_basis(proj, story, monkeypatch):
    mk(proj, story)
    PV.pin_section(proj, "investigations/sensitivity.qmd")
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f2"], "needs_work", "single phantom is not enough")
    as_human(proj)
    rep = PV.validate(proj)
    st = rep["claims"]["CLM-0001"]
    assert st["status"] in ("stale", "preliminary") and any("supported → preliminary" in m for m in st["stale"])


# ---------------------------------------------------------------- figures
def test_archived_figure_must_match_the_registered_artifact(proj, story):
    f = GS.make_figure(proj)
    PV.figure_add(proj, "archived", f, "investigations/sensitivity.qmd", "fig-sens", "Sensitivity", [story["art"]])
    assert PV.validate(proj)["figures"]["FIG-001"]["status"] == "ok"


def test_figure_that_is_not_the_artifact_is_rejected(proj, story):
    f = GS.make_figure(proj, same_as_artifact=False)
    PV.figure_add(proj, "archived", f, "investigations/sensitivity.qmd", "fig-sens", "Sensitivity", [story["art"]])
    assert "archive_mismatch" in codes(PV.validate(proj)["figures"]["FIG-001"])


def test_artifact_modified_after_figure_registered(proj, story):
    f = GS.make_figure(proj)
    PV.figure_add(proj, "archived", f, "investigations/sensitivity.qmd", "fig-sens", "Sensitivity", [story["art"]])
    with open(os.path.join(proj.root, "out", "sens.png"), "ab") as fh:
        fh.write(b"tamper")
    assert "artifact_modified" in codes(PV.validate(proj)["figures"]["FIG-001"])


def test_figure_file_edited_after_registration(proj, story):
    f = GS.make_figure(proj)
    PV.figure_add(proj, "archived", f, "investigations/sensitivity.qmd", "fig-sens", "Sensitivity", [story["art"]])
    open(os.path.join(proj.root, "docs", "scientist-guide", f), "ab").write(b"x")
    assert "figure_changed" in codes(PV.validate(proj)["figures"]["FIG-001"])


def test_derived_figure_needs_generator_and_conceptual_cannot_cite_data(proj, story):
    f = GS.make_figure(proj)
    PV.figure_add(proj, "derived", f, "a.qmd", "fig-d", "D", [f"{story['r2']}#sensitivity_ratio"])
    PV.figure_add(proj, "conceptual", f, "a.qmd", "fig-c", "C", [story["r1"]])
    rep = PV.validate(proj)["figures"]
    assert "figure_without_generator" in codes(rep["FIG-001"]) and "conceptual_with_data" in codes(rep["FIG-002"])


def test_generator_inputs_changed_marks_figure_stale(proj, story):
    f = GS.make_figure(proj)
    open(os.path.join(proj.root, "make_fig.py"), "w").write("print(1)\n")
    open(os.path.join(proj.root, "data.csv"), "w").write("a\n1\n")
    PV.figure_add(proj, "derived", f, "a.qmd", "fig-d", "D", [f"{story['r2']}#sensitivity_ratio"],
                  generator={"script": "make_fig.py", "inputs": ["data.csv"]})
    assert PV.validate(proj)["figures"]["FIG-001"]["status"] == "ok"
    open(os.path.join(proj.root, "data.csv"), "w").write("a\n2\n")
    assert PV.validate(proj)["figures"]["FIG-001"]["status"] == "stale"


def test_conceptual_figure_cannot_illustrate_an_observation(proj, story):
    f = GS.make_figure(proj)
    PV.figure_add(proj, "conceptual", f, "investigations/sensitivity.qmd", "fig-c", "Cartoon")
    ch = GS.write_chapter(proj)
    PV.claim_add(proj, "Measured", "observation", ch, [story["f2"]], figures=["FIG-001"])
    assert "illustration_as_evidence" in codes(PV.validate(proj)["claims"]["CLM-0001"])


# ---------------------------------------------------------------- prose consistency, evidence reuse, coverage
def test_claim_must_be_marked_in_existing_chapter_text(proj, story):
    ch = GS.write_chapter(proj, body="# S\n\nNo markers here.\n")
    PV.claim_add(proj, "Something", "observation", ch, [story["f2"]])
    assert any(i["code"] == "claim_not_in_text" for i in PV.validate(proj)["prose"])
    GS.write_chapter(proj, body="# S\n\nSomething [x]{#clm-0001 .claim} and [y]{#clm-0009 .claim}.\n")
    rep = PV.validate(proj)["prose"]
    assert [i["code"] for i in rep] == ["unregistered_claim"]


def test_same_evidence_in_several_chapters_is_tracked_independently(proj, story, monkeypatch):
    c1 = GS.write_chapter(proj, "a.qmd", "# A\n\n[x]{#clm-0001 .claim}\n")
    c2 = GS.write_chapter(proj, "b.qmd", "# B\n\n[x]{#clm-0002 .claim}\n")
    PV.claim_add(proj, "A", "observation", c1, [story["f2"]])
    PV.claim_add(proj, "B", "observation", c2, [story["f2"]])
    PV.pin_section(proj, "a.qmd")  # only A re-read
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f2"], "needs_work", "more phantoms needed")
    as_human(proj)
    rep = PV.validate(proj)["sections"]
    assert rep["a.qmd"]["state"] == "stale" and rep["b.qmd"]["state"] == "stale"
    PV.pin_section(proj, "a.qmd")
    rep = PV.validate(proj)["sections"]
    assert rep["a.qmd"]["state"] == "fresh" and rep["b.qmd"]["state"] == "stale"


def test_coverage_reports_experiments_and_findings_not_yet_cited(proj, story):
    cov = PV.validate(proj)["coverage"]
    assert set(cov["uncited_experiments"]) == {story["e1"], story["e2"]}
    mk(proj, story)
    cov = PV.validate(proj)["coverage"]
    assert cov["uncited_experiments"] == [story["e1"]] and cov["uncited_findings"] == [story["f1"]]


def test_validation_is_read_only_and_idempotent(proj, story):
    mk(proj, story)
    PV.pin_section(proj, "investigations/sensitivity.qmd")
    snap = lambda: {  # noqa: E731
        root + "/" + f: open(os.path.join(root, f), "rb").read()
        for root, _d, fs in os.walk(proj.path()) for f in fs if not f.endswith((".db", "-wal", "-shm"))
        for root in [root]}
    ev_before = proj.q("SELECT COUNT(*) AS n FROM events")[0]["n"]
    before = snap()
    a, b = PV.validate(proj), PV.validate(proj)
    assert a == b and snap() == before and proj.q("SELECT COUNT(*) AS n FROM events")[0]["n"] == ev_before


def test_unreadable_manifest_is_never_overwritten(proj, story):
    path = PV.prov_path(proj, "claims.json")
    os.makedirs(os.path.dirname(path))
    open(path, "w").write("{not json")
    with pytest.raises(ResearchError):
        PV.claim_add(proj, "x", "speculation", "a.qmd", [])
    assert open(path).read() == "{not json"


def test_pin_does_not_clear_a_retraction(proj, story, monkeypatch):
    mk(proj, story)
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f2"], "contradicted", "wrong")
    as_human(proj)
    PV.pin_section(proj, "investigations/sensitivity.qmd")
    assert PV.validate(proj)["claims"]["CLM-0001"]["status"] == "contradicted"


# ---------------------------------------------------------------- CLI
def test_cli_claim_figure_validate_roundtrip(proj, story, capsys):
    from research_cli.main import main
    GS.write_chapter(proj, "c.qmd", "# C\n\nText [x]{#clm-0001 .claim}.\n")
    assert main(["guide", "claim", "add", "Weak observability", "--kind", "observation", "--section", "c.qmd",
                 "--source", story["f2"], "--source", f"{story['r2']}#sensitivity_ratio"]) == 0
    assert main(["guide", "validate"]) == 0
    assert main(["guide", "claim", "show", "CLM-0001"]) == 0
    out = capsys.readouterr().out
    assert "CLM-0001" in out and "traceable" in out
    assert main(["guide", "claim", "add", "Bad", "--kind", "observation", "--section", "c.qmd", "--source", "F-404"]) == 4
    assert len(PV.load_claims(proj)) == 1  # nothing registered by the failed call
    assert main(["guide", "claim", "retire", "CLM-0001"]) == 0
    assert main(["guide", "validate", "--json"]) == 0  # a retired claim no longer needs evidence or a place in the text


def test_figure_refresh_after_intended_regeneration_keeps_history_and_does_not_hide_other_errors(proj, story):
    f = GS.make_figure(proj)
    open(os.path.join(proj.root, "make_fig.py"), "w").write("print(1)\n")
    open(os.path.join(proj.root, "data.csv"), "w").write("a\n1\n")
    PV.figure_add(proj, "derived", f, "a.qmd", "fig-d", "D", [f"{story['r2']}#sensitivity_ratio"],
                  generator={"script": "make_fig.py", "inputs": ["data.csv"]})
    old = PV.load_figures(proj)["FIG-001"]["file_sha256"]
    open(os.path.join(proj.root, "docs", "scientist-guide", f), "ab").write(b"regenerated")
    assert "figure_changed" in codes(PV.validate(proj)["figures"]["FIG-001"])
    r = PV.figure_refresh(proj, "fig-001", "re-plotted with larger labels")
    assert r["history"][-1]["file_sha256"] == old and r["file_sha256"] != old
    assert "figure_changed" not in codes(PV.validate(proj)["figures"]["FIG-001"])
    with pytest.raises(NotFound):
        PV.figure_refresh(proj, "FIG-099")
    # an archived figure that is not the artifact stays an error even after a refresh
    g = GS.make_figure(proj, same_as_artifact=False)
    PV.figure_add(proj, "archived", g, "a.qmd", "fig-a", "A", [story["art"]])
    PV.figure_refresh(proj, "FIG-002")
    assert "archive_mismatch" in codes(PV.validate(proj)["figures"]["FIG-002"])
