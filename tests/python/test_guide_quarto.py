import hashlib
import json
import os

import pytest

from research import guide_quarto as GQ
from research import provenance as PV
from research import verification as V
from research.util import ResearchError
from test_research_os import as_agent, as_human
import guide_story as GS

needs_quarto = pytest.mark.skipif(GQ.find_quarto() is None, reason="Quarto is not installed")

SCI_SENTENCE = "Structured perturbations alter the measurements far less than uniform ones"


@pytest.fixture
def story(proj, monkeypatch):
    return GS.build(proj, monkeypatch)


def snapshot(proj):
    """Hash of every canonical record file (not the rebuildable index, caches or logs of this tool)."""
    out = {}
    for root, dirs, files in os.walk(proj.path()):
        dirs[:] = [d for d in dirs if d not in ("cache", "bin")]
        for f in files:
            if f.endswith((".db", "-wal", "-shm")):
                continue
            path = os.path.join(root, f)
            out[os.path.relpath(path, proj.path())] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return out


def write_story_chapter(proj, story):
    GQ.init_quarto(proj)
    GQ.chapter_add(proj, "investigations/sensitivity.qmd", "Sensitivity to structured perturbations")
    fig = GS.make_figure(proj)
    PV.figure_add(proj, "archived", fig, "investigations/sensitivity.qmd", "fig-sens", "Sensitivity map", [story["art"]])
    GS.write_chapter(proj, "investigations/sensitivity.qmd", f"""# Sensitivity to structured perturbations

We model the measurements as $y = A x$, where the operator $A$ maps the unknown material distribution $x$ to the data.
{SCI_SENTENCE} [(sensitivity ratio about 0.04)]{{#clm-0001 .claim}}, so such perturbations are only weakly identifiable.

![The change in the data produced by structured perturbations is much smaller than for uniform perturbations. This is an
archived experimental figure from a single phantom.](../figures/sens.png){{#fig-sens fig-alt="Map of the change in the data"}}

As @fig-sens shows, the effect is small; with one phantom we cannot say it holds generally.

<!-- internal note about {story['f2']} -->

::: {{.content-visible when-profile="researcher"}}
Researcher-only remark about {story['f2']}.
:::
""")
    PV.claim_add(proj, "Structured perturbations alter the measurements much less than uniform ones (ratio ~0.04).",
                 "observation", "investigations/sensitivity.qmd", [story["f2"], f"{story['r2']}#sensitivity_ratio"], figures=["FIG-001"])
    PV.pin_section(proj, "investigations/sensitivity.qmd")


# ------------------------------------------------------------------ initialization
def test_init_fresh_project_creates_complete_guide_and_is_idempotent(proj):
    r = GQ.init_quarto(proj)
    gd = PV.guide_dir(proj)
    for f in ("_quarto.yml", "_quarto-scientific.yml", "_quarto-researcher.yml", "filters/edition.lua", "index.qmd", "briefing.qmd",
              "references.bib", ".gitignore"):
        assert os.path.exists(os.path.join(gd, f)), f
    assert r["left_untouched"] == [] and set(PV.load_sections(proj)) >= {"index.qmd", "briefing.qmd"}
    before = snapshot(proj)
    again = GQ.init_quarto(proj)
    assert again["created"] == [] and snapshot(proj) == before


def test_init_never_touches_existing_files_or_the_legacy_guide(proj):
    legacy = os.path.join(proj.root, "docs", "SCIENTIST_GUIDE.md")
    os.makedirs(os.path.dirname(legacy))
    open(legacy, "w").write("# My careful notes\n\nHuman text F-001 EXP-002.\n")
    gd = PV.guide_dir(proj)
    os.makedirs(gd)
    open(os.path.join(gd, "index.qmd"), "w").write("# Mine\n\nHand-written preface.\n")
    r = GQ.init_quarto(proj)
    assert "docs/scientist-guide/index.qmd" in r["left_untouched"]
    assert open(os.path.join(gd, "index.qmd")).read() == "# Mine\n\nHand-written preface.\n"
    assert open(legacy).read() == "# My careful notes\n\nHuman text F-001 EXP-002.\n"
    assert r["legacy_guide"] == "docs/SCIENTIST_GUIDE.md"


def test_interrupted_init_resumes(proj):
    GQ.init_quarto(proj)
    gd = PV.guide_dir(proj)
    for f in ("theory.qmd", "filters/edition.lua"):
        os.remove(os.path.join(gd, f))
    r = GQ.init_quarto(proj)
    assert sorted(r["created"]) == ["docs/scientist-guide/filters/edition.lua", "docs/scientist-guide/theory.qmd"]


def test_dry_run_changes_nothing(proj):
    r = GQ.init_quarto(proj, dry_run=True)
    assert r["dry_run"] and not os.path.exists(PV.guide_dir(proj))


def test_missing_quarto_gives_actionable_diagnostic_and_nothing_else_breaks(proj, story, monkeypatch):
    GQ.init_quarto(proj)
    monkeypatch.setattr(GQ, "find_quarto", lambda p=None: None)
    d = GQ.doctor(proj)
    assert not d["ok"] and "quarto.org" in " ".join(d["hints"])
    with pytest.raises(ResearchError) as e:
        GQ.build(proj, "scientific")
    assert "quarto.org" in (e.value.hint or "")
    assert PV.validate(proj)["ok"] and GQ.prepare(proj)["files_written"] >= 1  # everything not needing Quarto still works


# ------------------------------------------------------------------ source-level checks
def test_identifier_in_scientific_text_is_flagged_but_not_in_comments_or_researcher_blocks(proj, story):
    GQ.init_quarto(proj)
    GS.write_chapter(proj, "leaky.qmd", "# L\n\nSee EXP-002 and F-002/B1 for details.\n\n<!-- F-003 -->\n\n"
                     "::: {.content-visible when-profile=\"researcher\"}\nA-0001 here is fine.\n:::\n")
    GS.write_chapter(proj, "clean.qmd", "# C\n\nNo ids, just prose about [a claim]{#clm-0001 .claim}.\n")
    msgs = {i["message"] for i in GQ.source_issues(proj) if i["code"] == "identifier_in_scientific_text"}
    assert len(msgs) == 1
    m = next(iter(msgs))
    assert "leaky.qmd" in m and "EXP-002" in m and "F-003" not in m and "A-0001" not in m


def test_executable_cell_and_missing_caption_are_errors(proj, story):
    GQ.init_quarto(proj)
    GS.write_chapter(proj, "x.qmd", "# X\n\n```{python}\nprint(1)\n```\n\n![short](f.png){#fig-x}\n")
    codes = {i["code"] for i in GQ.source_issues(proj)}
    assert {"executable_cell", "figure_caption_missing"} <= codes


def test_unlisted_chapter_warns(proj):
    GQ.init_quarto(proj)
    GS.write_chapter(proj, "investigations/orphan.qmd", "# O\n\ntext\n")
    assert any(i["code"] == "chapter_not_in_book" for i in GQ.source_issues(proj))
    GQ.chapter_add(proj, "investigations/orphan.qmd")
    assert not any(i["code"] == "chapter_not_in_book" for i in GQ.source_issues(proj))
    assert "investigations/orphan.qmd" in GQ.listed_chapters(proj)


# ------------------------------------------------------------------ publishing (real Quarto)
@needs_quarto
def test_both_editions_share_prose_and_differ_only_in_provenance(proj, story):
    write_story_chapter(proj, story)
    sentinel = os.path.join(proj.root, "SENTINEL")
    before = snapshot(proj)
    sci = GQ.build(proj, "scientific")
    res = GQ.build(proj, "researcher")
    assert sci["ok"] and res["ok"], (sci, res)
    gd = PV.guide_dir(proj)

    def page(ed):
        return open(os.path.join(gd, "_output", ed, "investigations", "sensitivity.html"), encoding="utf-8").read()

    s_html, r_html = page("scientific"), page("researcher")
    # same scientific content in both
    for h in (s_html, r_html):
        assert SCI_SENTENCE in h and "y = A x" in h and "only weakly identifiable" in h
    # scientific edition: no identifiers anywhere visible, including the search index
    assert GQ.leak_check_output(os.path.join(gd, "_output", "scientific")) == []
    assert story["f2"] not in s_html and "CLM-0001" not in s_html and "Researcher-only remark" not in s_html
    # researcher edition: exact provenance, statuses, live values
    assert story["f2"] in r_html and "CLM-0001" in r_html and "Researcher-only remark" in r_html
    panel = r_html[r_html.find("Evidence for this chapter"):]
    assert "traceable" in panel and "0.04" in panel and "archived" in panel, panel[:1500] or r_html[-1500:]
    assert os.path.exists(os.path.join(gd, "_output", "researcher", "_generated", "evidence-status.html")) or "evidence-status" in "".join(
        os.listdir(os.path.join(gd, "_output", "researcher")))
    assert not os.path.exists(sentinel)
    assert {k: v for k, v in snapshot(proj).items()} == before  # building never modifies a research record


@needs_quarto
def test_rendering_never_executes_code(proj, story):
    GQ.init_quarto(proj)
    sentinel = os.path.join(proj.root, "SENTINEL")
    GS.write_chapter(proj, "boom.qmd", f"# Boom\n\n```{{python}}\nopen({sentinel!r}, 'w').write('ran')\n```\n")
    GQ.chapter_add(proj, "boom.qmd")
    GQ.build(proj, "researcher")  # may be flagged by validation but must not execute
    assert not os.path.exists(sentinel)
    with pytest.raises(ResearchError):  # the scientific edition refuses over the executable-cell error
        GQ.build(proj, "scientific")


@needs_quarto
def test_scientific_edition_refuses_to_publish_a_retracted_claim_researcher_shows_it(proj, story, monkeypatch):
    write_story_chapter(proj, story)
    as_agent(proj, "claude", "verifier", monkeypatch)
    V.review_finding(proj, story["f2"], "contradicted", "The ratio was an artefact of the phantom.")
    as_human(proj)
    with pytest.raises(ResearchError) as e:
        GQ.build(proj, "scientific")
    assert "error" in str(e.value)
    r = GQ.build(proj, "researcher")
    assert r["ok"] and r["validation"]["errors"] >= 1
    h = open(os.path.join(PV.guide_dir(proj), "_output", "researcher", "investigations", "sensitivity.html"), encoding="utf-8").read()
    assert "contradicted" in h
    GQ.build(proj, "scientific", allow_invalid=True)  # explicit override exists


@needs_quarto
def test_pdf_failure_leaves_html_intact(proj, story, monkeypatch):
    write_story_chapter(proj, story)
    real = GQ._run

    def fake(argv, cwd=None, timeout=600):
        if "typst" in argv and "render" in argv:
            return 1, "typst exploded"
        return real(argv, cwd, timeout)

    monkeypatch.setattr(GQ, "_run", fake)
    r = GQ.build(proj, "scientific", pdf=True)
    assert r["ok"] and r["pdf"]["ok"] is False and any("PDF failed" in w for w in r["warnings"])
    assert os.path.exists(os.path.join(PV.guide_dir(proj), "_output", "scientific", "index.html"))


@needs_quarto
def test_real_pdf_builds_where_typst_is_available(proj, story):
    write_story_chapter(proj, story)
    if not GQ.doctor(proj)["pdf"]:
        pytest.skip("Typst unavailable")
    r = GQ.build(proj, "scientific", pdf=True)
    assert r["ok"]
    if not r["pdf"]["ok"] and "network" in r["pdf"]["log"].lower():
        pytest.skip("Typst template package could not be downloaded (offline)")
    assert r["pdf"]["ok"], r["pdf"]["log"]
    out = os.path.join(PV.guide_dir(proj), "_output", "scientific")
    assert os.path.exists(os.path.join(out, "index.html")), "building the PDF must not remove the HTML"
    assert r["pdf"]["file"].endswith(".pdf") and os.path.exists(os.path.join(proj.root, r["pdf"]["file"]))
    assert not os.path.exists(os.path.join(PV.guide_dir(proj), "_output", "_pdf-scientific"))


@needs_quarto
def test_prepare_is_idempotent_and_confined_to_generated(proj, story):
    write_story_chapter(proj, story)
    GQ.prepare(proj)
    gd = PV.guide_dir(proj)
    prose = {f: open(os.path.join(gd, f), "rb").read() for f in ("index.qmd", "investigations/sensitivity.qmd", "_quarto.yml")}
    second = GQ.prepare(proj)
    assert second["files_written"] == 0
    assert prose == {f: open(os.path.join(gd, f), "rb").read() for f in prose}


@needs_quarto
def test_researcher_pdf_is_skipped_with_a_warning_and_html_is_unaffected(proj, story):
    write_story_chapter(proj, story)
    r = GQ.build(proj, "researcher", pdf=True)
    assert r["ok"] and "pdf" not in r and any("scientific edition" in w for w in r["warnings"])
    html = open(os.path.join(PV.guide_dir(proj), "_output", "researcher", "investigations", "sensitivity.html"), encoding="utf-8").read()
    assert "#ev-clm-0001" in html  # HTML keeps the claim → evidence links
