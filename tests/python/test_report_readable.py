"""`research report` and `report --full`: readable, escaped, navigable, still deterministic."""
import os
import re

import pytest

from research import fullreport as FR
from research import report as RP
from research import services as S
from research import verification as V
from conftest import as_agent, as_human
import guide_story as GS
import sys
from research import runs as R


@pytest.fixture
def story(proj, monkeypatch):
    return GS.build(proj, monkeypatch)


def html_of(proj, scope=None):
    t, blocks = RP.build(proj, scope, proj.path("reports"))
    return RP.to_html(t, blocks), RP.to_markdown(blocks)


def test_html_is_escaped_navigable_and_summarised(proj, story, monkeypatch):
    as_agent(proj, "claude", "implementer", monkeypatch)
    S.create_finding(proj, "Bad <script>alert(1)</script> title", statement="x < y & \"quoted\" <img src=x onerror=alert(1)>",
                     supports=[story["r1"]])
    as_human(proj)
    h, md = html_of(proj)
    assert "<script>" not in h and "onerror=alert" not in h.replace("&lt;img src=x onerror=alert(1)&gt;", "")
    assert "&lt;script&gt;" in h and "x &lt; y &amp;" in h
    assert '<nav class="toc"' in h and re.search(r'<h2 id="what-we-learned">', h) and 'href="#what-we-learned"' in h
    assert 'class="glance"' in h and "Findings supported" in h and 'class="tile ok"' in h
    assert 'class="badge b-supported"' in h and 'alt="' in h and 'loading="lazy"' in h
    assert re.findall(r'<td class="t-ok">VERIFIED</td>', h)  # numbers re-verified, coloured
    assert "@media print" in h and "prefers-color-scheme:dark" in h
    ids = re.findall(r' id="([^"]+)"', h)
    assert len(ids) == len(set(ids))  # anchors are unique even with repeated headings


def test_markdown_has_a_glance_table_and_contents(proj, story):
    _, md = html_of(proj)
    assert re.search(r"\| Experiments \| Runs \| Findings supported \|", md) and "**Contents:** [Where things stand]" not in md
    assert "[What we learned](#what-we-learned)" in md
    scoped = html_of(proj, story["q"])[1]
    assert "| Experiments |" in scoped and "Open questions" not in scoped.split("\n\n")[3]  # a scoped report omits the project-wide tile


def test_glance_counts_match_the_records(proj, story):
    tiles = {t[0]: t[1] for t in RP._glance(proj, None)}
    assert tiles["Experiments"] == "2" and tiles["Findings supported"] == "2 of 2" and tiles["Contradicted"] == "0"


def test_full_report_gains_contents_and_stays_deterministic_and_complete(proj, story):
    snap, text = FR.export(proj)
    assert "**At a glance:**" in text and "[1. Export Metadata](#1-export-metadata)" in text
    assert "[12. Audit / Event History](#12-audit--event-history)" in text and "How to read it" in text
    assert FR.export(proj)[1] == text
    cov = FR.coverage(snap, text)
    assert cov["ok"], cov
    for s in FR.SECTIONS:
        assert f"## {s}" in text


def _exp_with_runs(p, repo, n=2):
    q = S.create_question(p, "Does alpha matter?")
    e = S.create_experiment(p, "alpha sweep", question=q["id"], parameters={"alpha": [0.1, 0.5]}, planned_runs=6)
    (repo / "sim.py").write_text(
        "import research,sys\na=float(sys.argv[1])\nresearch.log_metric('err', abs(a-0.5))\n"
        "open(f'out_{a}.png','wb').write(b'\\x89PNG\\r\\n')\nresearch.register_artifact(f'out_{a}.png', description='plot')\n")
    runs = [R.exec_run(p, e["id"], f"{sys.executable} sim.py {a}", parameters={"alpha": a}, tee=False)
            for a in (0.1, 0.5)[:n]]
    return q, e, runs


def test_report_md_and_html(proj, repo, monkeypatch):
    q, e, runs = _exp_with_runs(proj, repo)
    arts = R.list_artifacts(proj, run=runs[1]["id"])
    f = S.create_finding(proj, "alpha 0.5 is best", supports=[runs[1]["id"], arts[0]["id"]], limitations="one seed")
    S.synthesize(proj, e["id"], interpretation="U-shaped", next_experiment="probe 1.0-1.5")
    out = RP.write_report(proj, None, "both")
    md = (repo / ".research/reports/project.md").read_text()
    assert "## What we learned" in md and "alpha 0.5 is best" in md and "| run | status | alpha | err |" in md
    assert "](../../out_0.5.png)" in md and "U-shaped" in md
    html = (repo / ".research/reports/project.html").read_text()
    assert 'src="../../out_0.5.png"' in html and "<table>" in html and 'class="tbl-wrap"' in html
    scoped = RP.write_report(proj, q["id"], "md")
    assert scoped["paths"] == [".research/reports/Q-001.md"]
    custom = repo / "docs" / "r.md"
    RP.write_report(proj, e["id"], "md", str(custom))
    assert "](../out_0.5.png)" in custom.read_text()  # image paths follow the output folder
