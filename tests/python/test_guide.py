"""The Scientist Guide: a Quarto book on main. Scaffold, important results ("highlights"), logging, bounded agent drafts,
citations, publish workflow, triggers. (Quarto itself is exercised in test_guide_quarto.py.)"""
import json
import os
import re
import subprocess

import pytest

from research import guide as G
from research import guide_quarto as GQ
from research import provenance as PV
from research import services as S
from research import verification as V
from research.util import NotFound, ResearchError
from test_research_os import as_agent, as_human
import guide_story as GS


@pytest.fixture
def story(proj, monkeypatch):
    return GS.build(proj, monkeypatch)


def read(proj, rel):
    return open(os.path.join(PV.guide_dir(proj), rel), encoding="utf-8").read()


def test_init_creates_a_complete_editable_guide_and_a_publish_workflow(proj, repo):
    r = G.init(proj)
    gd = os.path.join(str(repo), "docs", "scientist-guide")
    for f in ("_quarto.yml", "theme.scss", "references.bib", "references.qmd", "ideas.qmd", "results.qmd", "briefing.qmd", "filters/edition.lua"):
        assert os.path.exists(os.path.join(gd, f)), f
    q = read(proj, "_quarto.yml")
    assert "theme: [cosmo, theme.scss]" in q and "bibliography: references.bib" in q and "- results.qmd" in q and "- references.qmd" in q
    wf = (repo / ".github" / "workflows" / "publish-guide.yml").read_text()
    assert "branches: [main]" in wf and "docs/scientist-guide" in wf and "--profile scientific" in wf and "deploy-pages" in wf
    assert r["workflow"] and not r["left_untouched"]
    secs = PV.load_sections(proj)
    assert secs["ideas.qmd"]["owner"] == "human" and secs["references.qmd"]["owner"] == "human" and secs["results.qmd"]["owner"] == "agent"
    again = G.init(proj)  # never overwrites
    assert not again["created"] and again["left_untouched"]
    (repo / "docs" / "scientist-guide" / "theme.scss").write_text("// mine\n")
    G.init(proj)
    assert (repo / "docs" / "scientist-guide" / "theme.scss").read_text() == "// mine\n"


def test_workflow_can_be_declined_and_is_never_deleted(proj, repo):
    r = G.init(proj, workflow=False)
    assert not r["workflow"] and not (repo / ".github").exists()
    assert not G.publishing(proj)["workflow"]


def test_the_guide_lives_on_main_even_when_opened_from_an_experiment_worktree(proj, repo, tmp_path):
    from research import workspace as W
    from research.store import Project
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    proj.close()
    W.enable(str(repo), str(tmp_path / "ledger"))
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "exp/one", str(wt)], cwd=repo, check=True, env=env)
    p2 = Project(str(wt), sync=False)
    try:
        assert os.path.realpath(PV.guide_dir(p2)) == os.path.realpath(os.path.join(repo, "docs", "scientist-guide"))
        G.init(p2)
        assert (repo / "docs" / "scientist-guide" / "_quarto.yml").exists() and not (wt / "docs" / "scientist-guide").exists()
    finally:
        p2.close()


def test_disabled_or_missing_guide_is_inert(proj):
    st = G.status(proj)
    assert st["exists"] is False and st["highlights"] == [] and G.attention(proj) == []
    assert G.log(proj) == {"skipped": "no guide"}
    with pytest.raises(NotFound):
        G.draft(proj)
    with pytest.raises(NotFound):
        G.cite_list(proj)


# ------------------------------------------------------------------ important results

def test_highlight_rules(proj, story):
    assert {h["id"] for h in G.highlights(proj)} == {story["f1"], story["f2"]}  # both supported
    extra = S.create_finding(proj, "A preliminary idea", statement="maybe", supports=[story["r1"]])["id"]
    assert extra not in {h["id"] for h in G.highlights(proj)}
    proj.save_config({"guide.highlights": "all"})
    proj.config = proj.load_config()
    assert extra in {h["id"] for h in G.highlights(proj)}
    proj.save_config({"guide.highlights": "starred"})
    proj.config = proj.load_config()
    assert G.highlights(proj) == []
    G.init(proj)
    r = G.star(proj, extra)
    assert r["starred"] and [h["id"] for h in G.highlights(proj)] == [extra]
    assert G.highlights(proj)[0]["starred"] and proj.get("finding", extra)["tags"] == ["highlight"]
    G.star(proj, extra, False)
    assert G.highlights(proj) == []


def test_superseded_never_and_contradicted_only_when_starred(proj, story, monkeypatch):
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f1"], "contradicted", "Does not hold")
    as_human(proj)
    assert story["f1"] not in {h["id"] for h in G.highlights(proj)}
    G.init(proj)
    G.star(proj, story["f1"])
    assert story["f1"] in {h["id"] for h in G.highlights(proj)}


# ------------------------------------------------------------------ logging into results.qmd

def test_log_adds_one_entry_per_result_without_identifiers_and_registers_provenance(proj, story):
    G.init(proj)
    r = G.log(proj)
    assert set(r["logged"]) == {story["f1"], story["f2"]} and r["chapter"].endswith("results.qmd")
    t = read(proj, "results.qmd")
    for fid in (story["f1"], story["f2"]):
        assert f"<!-- research:result {fid} -->" in t and f"<!-- /research:result -->" in t
    assert "{#clm-0001 .claim}" in t and "{#clm-0002 .claim}" in t
    claims = PV.load_claims(proj)
    assert {c["section"] for c in claims.values()} == {"results.qmd"} and claims["CLM-0001"]["sources"][0]["ref"] == story["f1"]
    # the scientific reading shows no Research OS identifier, even though the finding statements mention the work
    assert not [i for i in GQ.source_issues(proj) if i["code"] == "identifier_in_scientific_text"]
    assert "DRAFT" in t  # the slot the researcher (or an agent) fills
    assert PV.validate(proj)["errors"] == 0


def test_log_is_idempotent_never_rewrites_and_a_deleted_entry_stays_deleted(proj, story):
    G.init(proj)
    G.log(proj)
    p = os.path.join(PV.guide_dir(proj), "results.qmd")
    mine = open(p).read().replace("DRAFT: replace this paragraph", "My own words, edited. DRAFT: replace this paragraph")
    open(p, "w").write(mine)
    assert G.log(proj)["logged"] == []
    assert open(p).read() == mine
    # the researcher removes an entry: it does not come back
    cut = re.sub(r"<!-- research:result F-001 -->.*?<!-- /research:result -->\n", "", mine, flags=re.S)
    open(p, "w").write(cut)
    G.log(proj)
    assert "research:result F-001" not in open(p).read()


def test_log_copies_and_registers_the_result_plot_and_respects_max_figures(proj, story):
    G.init(proj)
    G.log(proj, [story["f2"]])  # f2 is supported by an image artifact
    t = read(proj, "results.qmd")
    m = re.search(r"!\[(?P<cap>[^\]]+)\]\((?P<src>figures/[^)]+)\)\{#(?P<label>fig-[\w-]+) fig-alt=\"[^\"]+\"\}", t)
    assert m and len(m.group("cap")) >= 20 and os.path.exists(os.path.join(PV.guide_dir(proj), m.group("src")))
    figs = PV.load_figures(proj)
    assert list(figs.values())[0]["kind"] == "archived" and list(figs.values())[0]["sources"][0]["ref"] == story["art"]
    assert not [i for i in GQ.source_issues(proj) if i["code"] in ("figure_caption_missing", "identifier_in_scientific_text")]
    proj2 = proj
    proj2.save_config({"guide.draft.max_figures": 0})
    proj2.config = proj2.load_config()
    G.log(proj2, [story["f1"]])
    assert t.count("![") == read(proj, "results.qmd").count("![")  # f1 would have had none anyway; the cap is honoured below
    assert G.settings(proj2)["draft"]["max_figures"] == 0


def test_a_supported_review_logs_automatically_and_auto_log_can_be_turned_off(proj, monkeypatch):
    G.init(proj)
    # Agents cannot create experiments directly (governance), so a human sets up the experiment and run.
    rid = GS.R.create_run(proj, S.create_experiment(proj, "e")["id"], command="x", status="completed")["id"]
    as_agent(proj, "claude", "implementer", monkeypatch)
    f = S.create_finding(proj, "Alpha matters", statement="Alpha changes the outcome", supports=[rid])["id"]
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, f, "supported", "ok")
    assert f"research:result {f}" in read(proj, "results.qmd")
    as_agent(proj, "claude", "implementer", monkeypatch)
    proj.save_config({"guide.auto_log": False})
    proj.config = proj.load_config()
    f2 = S.create_finding(proj, "Beta matters", statement="Beta too", supports=[proj.q("SELECT id FROM runs")[0]["id"]])["id"]
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, f2, "supported", "ok")
    assert f"research:result {f2}" not in read(proj, "results.qmd")
    assert f2 in {h["id"] for h in G.highlights(proj)} and not [h for h in G.highlights(proj) if h["id"] == f2][0]["logged"]


def test_a_human_owned_results_chapter_receives_proposals_not_edits(proj, story):
    G.init(proj)
    PV.section_set(proj, "results.qmd", owner="human")
    before = read(proj, "results.qmd")
    r = G.log(proj)
    assert r["proposal"] and read(proj, "results.qmd") == before
    assert "research:result" in read(proj, "_proposals/results.md")


# ------------------------------------------------------------------ bounded agent drafts

def test_draft_prepares_a_bounded_brief_and_records_a_baseline(proj, story):
    proj.save_config({"guide.draft.max_words": 90, "guide.draft.max_sections": 1, "guide.model": "claude-opus-5-5"})
    proj.config = proj.load_config()
    G.init(proj)
    d = G.draft(proj)
    brief = open(d["brief"] if "brief" in d else d["brief_path"], encoding="utf-8").read() if ("brief" in d or "brief_path" in d) else json.dumps(d)
    assert d["targets"] == [story["f1"]] and d["limits"]["max_words"] == 90  # max_sections = 1
    assert "at most 90 words per section" in brief and "ideas.qmd" in brief and "scientific-briefing" in brief
    assert os.path.exists(PV.prov_path(proj, "draft.json"))
    assert "research:result" in read(proj, "results.qmd")  # the entries exist before the agent is pointed at them


def test_draft_with_nothing_to_draft_says_so(proj, story):
    G.init(proj)
    G.log(proj)
    G.draft_done(proj)
    assert G.draft(proj)["nothing_to_draft"] is True and G.status(proj)["undrafted"] == 0


def test_draft_check_enforces_words_sections_figures_and_human_chapters(proj, story):
    proj.save_config({"guide.draft.max_words": 50, "guide.draft.max_sections": 1, "guide.draft.max_figures": 0})
    proj.config = proj.load_config()
    G.init(proj)
    G.draft(proj, results=[story["f1"]])
    assert G.draft_check(proj)["ok"]
    p = os.path.join(PV.guide_dir(proj), "results.qmd")
    short = open(p).read().replace("<!-- DRAFT", "We measured the error of the reconstruction and found it small. <!-- DRAFT")
    open(p, "w").write(short)
    assert G.draft_check(proj)["ok"]
    open(p, "w").write(short.replace("found it small.", "found it small. " + "word " * 80))
    c = G.draft_check(proj)
    assert not c["ok"] and any("words added" in x for x in c["problems"])
    open(p, "w").write(short)
    open(os.path.join(PV.guide_dir(proj), "ideas.qmd"), "a").write("\nThe agent wrote here.\n")
    c = G.draft_check(proj)
    assert not c["ok"] and any("ideas.qmd is human-owned" in x for x in c["problems"])
    with open(os.path.join(PV.guide_dir(proj), "briefing.qmd"), "a") as f:
        f.write("\nMore.\n")
    c = G.draft_check(proj)
    assert any("chapters changed" in x for x in c["problems"])
    open(p, "w").write(short + "\n![A figure that the agent added in violation of the limit of zero figures](figures/x.png)\n")
    assert any("figure(s) added" in x for x in G.draft_check(proj)["problems"])


def test_draft_done_validates_ids_and_clears_the_reminder(proj, story):
    G.init(proj)
    G.log(proj)
    assert G.status(proj)["undrafted"] == 2
    with pytest.raises(NotFound):
        G.draft_done(proj, ["F-999"])
    r = G.draft_done(proj, [story["f1"]])
    assert r["drafted"] == [story["f1"]] and G.status(proj)["undrafted"] == 1
    G.draft_done(proj)
    assert G.status(proj)["undrafted"] == 0 and G.attention(proj) == []


def test_triggers_shape_the_reminder_only(proj, story):
    G.init(proj)
    G.log(proj)
    assert G.attention(proj)[0]["kind"] == "guide_draft"  # default: result
    proj.save_config({"guide.draft.trigger": "manual"})
    proj.config = proj.load_config()
    assert G.attention(proj) == []
    proj.save_config({"guide.draft.trigger": "checkpoint"})
    proj.config = proj.load_config()
    assert G.attention(proj) == []  # no checkpoint since the results were logged
    from research import context as C
    C.create_checkpoint(proj, title="where we are", understanding="alpha matters", use_draft=False)
    assert G.attention(proj)[0]["kind"] == "guide_draft"  # a checkpoint after the results were logged releases the reminder
    proj.save_config({"guide.draft.trigger": "result"})
    proj.config = proj.load_config()
    assert any(i["kind"] == "guide_draft" for i in C.research_home(proj)["attention_items"])  # and it reaches Research Home


# ------------------------------------------------------------------ citations

BIB = """@article{lee2021alpha,
  author = {Lee, Wei and Smith, Jane},
  title = {Alpha in {CT} reconstruction},
  journal = {J. Imaging},
  year = {2021},
  doi = {10.1000/xyz}
}"""


def test_cite_add_list_and_check(proj):
    G.init(proj)
    r = G.cite_add(proj, bibtex=BIB)
    assert r["added"] == ["lee2021alpha"] and r["cite_as"] == "[@lee2021alpha]"
    e = G.cite_list(proj)["entries"][0]
    assert e["year"] == "2021" and e["doi"] == "10.1000/xyz" and "CT" in e["title"] and e["authors"].startswith("Lee")
    with pytest.raises(ResearchError, match="Already in references.bib"):
        G.cite_add(proj, bibtex=BIB)
    with pytest.raises(ResearchError, match="does not look like BibTeX"):
        G.cite_add(proj, bibtex="just some text")
    with pytest.raises(ResearchError, match="unbalanced"):
        G.cite_add(proj, bibtex="@article{k1, title = {x")
    m = G.cite_add(proj, title="Bayesian imaging", authors="Park, Min", year=2019, journal="Nature Methods")
    assert m["added"] == ["park2019bayesian"]
    assert G.cite_add(proj, title="A web note", url="https://x.org")["added"][0].startswith("ref")  # no author: still a usable key
    assert G.bib_entries(read(proj, "references.bib")).keys() >= {"lee2021alpha", "park2019bayesian"}
    assert "% @article{smith2020example" in read(proj, "references.bib")  # the commented example is not an entry
    assert "smith2020example" not in G.bib_entries(read(proj, "references.bib"))


def test_missing_citations_are_errors_unused_are_listed_and_crossrefs_are_not_citations(proj):
    G.init(proj)
    G.cite_add(proj, bibtex=BIB)
    p = os.path.join(PV.guide_dir(proj), "ideas.qmd")
    open(p, "a").write("\nSee [@lee2021alpha] and [@ghost2000] and @fig-x and @eq-y, mail me@example.org.\n")
    c = G.cite_list(proj)
    assert list(c["missing"]) == ["ghost2000"] and c["unused"] == [] and c["entries"][0]["cited_in"] == ["ideas.qmd"]
    errs = [i for i in GQ.source_issues(proj) if i["code"] == "citation_missing"]
    assert len(errs) == 1 and "ghost2000" in errs[0]["message"]
    assert PV.validate(proj)["errors"] >= 1
    assert G.status(proj)["citations"]["missing"] == {"ghost2000": ["ideas.qmd"]}


# ------------------------------------------------------------------ status, CLI, RPC, MCP

def test_status_and_overview_describe_the_guide(proj, story):
    G.init(proj)
    G.log(proj)
    st = G.overview(proj)
    assert st["exists"] and st["path"] == "docs/scientist-guide" and len(st["highlights"]) == 2 and st["undrafted"] == 2
    assert st["publishing"]["workflow"] and st["citations"]["entries"] == 0 and st["draft"]["prepared"] is False
    assert st["provenance"]["sections"]["results.qmd"]["owner"] == "agent" and "quarto" in st and st["review"] is not None
    assert st["settings"]["draft"]["max_words"] == 150


def test_cli_flow(proj, story, repo, capsys):
    from research_cli.main import main
    proj.close()
    run = lambda *a: main(["--root", str(repo), *a])  # noqa: E731
    assert run("guide") == 0 and "No Scientist Guide yet" in capsys.readouterr().out
    assert run("guide", "init") == 0 and "Scientist Guide in docs/scientist-guide" in capsys.readouterr().out
    assert run("guide", "log") == 0 and "logged" in capsys.readouterr().out
    assert run("guide", "highlights") == 0 and "F-001" in capsys.readouterr().out
    assert run("guide", "cite", "add", "--bibtex", BIB) == 0 and "[@lee2021alpha]" in capsys.readouterr().out
    assert run("guide", "cite", "check") == 0
    assert run("guide", "publish") == 0 and "publish-guide.yml" in capsys.readouterr().out
    assert run("guide", "draft", "check") == 0 and "No draft" in capsys.readouterr().out
    assert run("guide", "draft", "F-002") == 0 and "draft prepared for F-002" in capsys.readouterr().out
    assert run("guide", "draft", "done", "F-002") == 0 and "recorded: F-002" in capsys.readouterr().out
    assert run("guide", "draft", "check") == 0 and "within the limits" in capsys.readouterr().out
    assert run("guide", "unstar", "F-001") == 0
    capsys.readouterr()
    assert run("guide", "--json") == 0 and json.loads(capsys.readouterr().out)["exists"] is True
    assert run("guide", "cite", "list") == 0


def test_rpc_guide_methods_and_what_is_read_only(proj, story, repo):
    from research.api import OPS
    from test_plans_tasks_interfaces import rpc
    proj.close()
    r = rpc(repo, ("guide_init", {}), ("guide_log", {}), ("guide_status", {}), ("guide_highlight", {"id": story["f1"], "on": False}),
            ("guide_cite_add", {"bibtex": BIB}), ("guide_cite_list", {}),
            ("guide_draft_done", {"ids": [story["f1"]]}))
    assert all("result" in x for x in r.values()), r
    assert r[3]["result"]["undrafted"] == 2 and len(r[3]["result"]["highlights"]) == 2 and r[6]["result"]["entries"][0]["key"] == "lee2021alpha"
    assert {n for n, o in OPS.items() if n.startswith("guide_") and o.mutates} >= {"guide_init", "guide_build", "guide_highlight", "guide_log", "guide_draft", "guide_draft_done", "guide_cite_add"}
    d = rpc(repo, ("guide_draft", {"results": [story["f2"]]}))[1]["result"]
    assert d["targets"] == [story["f2"]] and d["shell"] is None and d["limits"]["max_words"] == 150


def test_mcp_report_and_guide_tools(proj, story, repo):
    from research.mcp import _tools
    tools = {t.name: t for t in _tools()}
    assert tools["report"].mutating and not tools["guide_highlights"].mutating and not tools["guide_draft_check"].mutating
    G.init(proj)
    out = tools["report"].fn(proj, scope=None, fmt="both")
    assert out["paths"] == [".research/reports/project.md", ".research/reports/project.html"]
    assert (repo / ".research/reports/project.html").exists()
    scoped = tools["report"].fn(proj, scope=story["q"], fmt="md")
    assert scoped["paths"] == [f".research/reports/{story['q']}.md"]
    assert {h["id"] for h in tools["guide_highlights"].fn(proj)} == {story["f1"], story["f2"]}
    G.log(proj)
    assert tools["guide_draft_done"].fn(proj, ids=[story["f1"]])["drafted"] == [story["f1"]]
    assert tools["guide_draft_check"].fn(proj)["ok"] in (True, False)
    assert "guide_status" in tools and tools["guide_status"].fn(proj)["exists"]


# ------------------------------------------------------------------ a prepared draft actually runs (fake `claude` stands in for the model)

FAKE_CLAUDE = """#!/usr/bin/env python3
import os, re, subprocess, sys
brief = open(os.environ["RESEARCH_BRIEF"], encoding="utf-8").read()
assert "at most 120 words per section" in brief and "ideas.qmd" in brief and "scientific-briefing" in brief
root = os.environ["RESEARCH_ROOT"]
path = os.path.join(root, "docs", "scientist-guide", "results.qmd")
text = open(path, encoding="utf-8").read()
text = re.sub(r"<!-- DRAFT:.*?-->", os.environ.get("FAKE_TEXT", "A short draft."), text, count=1, flags=re.S)
open(path, "w", encoding="utf-8").write(text)
run = lambda *a: subprocess.run(["research", *a], cwd=root, capture_output=True, text=True)
for fid in re.findall(r"F-\\d+", re.findall(r"draft done (F-\\d+(?: F-\\d+)*)", brief)[-1]):
    r = run("guide", "draft", "done", fid)
    sys.stdout.write(r.stdout + r.stderr)
r = run("guide", "draft", "check")
sys.stdout.write(r.stdout + r.stderr)
sys.exit(r.returncode)
"""


def _run_prepared(proj, d, repo, tmp_path, text):
    import stat
    import sys as _sys
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    f = bindir / "claude"
    f.write_text(FAKE_CLAUDE)
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    shim = bindir / "research"
    shim.write_text(f"#!/bin/sh\nexec {_sys.executable} -m research_cli --root {repo} \"$@\"\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, **d["env"], "FAKE_TEXT": text, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    argv = ["claude"] + d["argv"][1:]
    return subprocess.run(argv, cwd=d["cwd"] or str(repo), env=env, capture_output=True, text=True, timeout=120)


def test_a_prepared_draft_runs_and_the_check_passes_within_the_limits(proj, story, repo, tmp_path):
    proj.save_config({"guide.draft.max_words": 120})
    proj.config = proj.load_config()
    G.init(proj)
    d = G.draft(proj, results=[story["f1"]])
    assert d["argv"][0] == "claude" and d["env"]["RESEARCH_BRIEF"]
    r = _run_prepared(proj, d, repo, tmp_path, "We varied the setup and measured the error; it was small for the uniform case, with one phantom as the limit.")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "within the limits" in r.stdout and "recorded" in r.stdout
    assert G.status(proj)["undrafted"] == 1 and "We varied the setup" in read(proj, "results.qmd")


def test_a_prepared_draft_that_overruns_is_caught_by_the_check(proj, story, repo, tmp_path):
    proj.save_config({"guide.draft.max_words": 120})
    proj.config = proj.load_config()
    G.init(proj)
    d = G.draft(proj, results=[story["f1"]])
    r = _run_prepared(proj, d, repo, tmp_path, "word " * 400)
    assert r.returncode == 1 and "exceeds the limits" in r.stdout and "words added" in r.stdout
