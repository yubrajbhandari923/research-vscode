"""The research website: the Scientist Guide plus experiment pages. Real git and (where installed) real Quarto; no GitHub, no network, no push."""
import os
import re
import subprocess

import pytest

from conftest import git
from research import guide_quarto as GQ, provenance as PV, services, site
from research.util import ResearchError

needs_quarto = pytest.mark.skipif(GQ.find_quarto() is None, reason="Quarto is not installed")

QMD = """# Smoke test of the diffusion model

The heat equation spreads an initial spike; the width grows with the square root of time.

```{python}
run_the_simulation()
```

![Temperature profiles at three times: the spike spreads and flattens, as the diffusion equation predicts.](../figures/profile.png){#fig-profile fig-alt="Three curves"}

As @fig-profile shows, the curves widen over time.
"""


def commit_reports(repo, extra=None):
    files = {"reports/quarto/chapters/00-smoke.qmd": QMD, "reports/quarto/figures/profile.png": "PNGDATA", "reports/quarto/figures/table.csv": "t,x\n1,2\n"}
    files.update(extra or {})
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "-f", ".")
    git(repo, "commit", "-qm", "reports")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def exp(proj):
    return services.create_experiment(proj, "Diffusion smoke test", hypothesis="The width grows like sqrt(t).", method="Explicit finite differences on a 1-D grid.")["id"]


def snapshot(root):
    out = {}
    for d, _, fs in os.walk(root):
        if ".git" in d.split(os.sep) or "_output" in d.split(os.sep) or ".quarto" in d.split(os.sep):
            continue
        for f in fs:
            out[os.path.relpath(os.path.join(d, f), root)] = open(os.path.join(d, f), "rb").read()
    return out


# ----------------------------------------------------------------------------------------------- scaffold, migration, idempotency
def test_init_previews_then_creates_the_site_and_never_overwrites(proj, repo):
    before = snapshot(repo)
    r = site.init(proj)
    assert r["dry_run"] and snapshot(repo) == before and any(f["path"].endswith("about.qmd") for f in r["files"])
    r = site.init(proj, apply=True)
    gd = PV.guide_dir(proj)
    for f in ("_quarto.yml", "theme.scss", "references.bib", ".gitignore", "index.qmd", "about.qmd", "research-question.qmd"):
        assert os.path.exists(os.path.join(gd, f)), f
    assert os.path.exists(os.path.join(repo, ".github", "workflows", "publish-guide.yml"))
    assert {"about.qmd", "research-question.qmd"} <= set(GQ.listed_chapters(proj))
    assert "Understand alpha" in open(os.path.join(gd, "about.qmd")).read() and "TODO" in open(os.path.join(gd, "about.qmd")).read()
    # you edit a page; running init again (a repair) changes nothing and reports the file as untouched
    about = os.path.join(gd, "about.qmd")
    open(about, "a").write("\nMy own paragraph.\n")
    snap = snapshot(repo)
    again = site.init(proj, apply=True)
    assert snapshot(repo) == snap and again["created"] == [] and again["pages"]["created"] == []


def test_existing_guide_is_migrated_without_touching_it_and_a_missing_workflow_is_restored(proj, repo):
    GQ.init_quarto(proj, workflow=False)                       # an older project: a guide, no site pages, no workflow
    gd = PV.guide_dir(proj)
    idx = os.path.join(gd, "index.qmd")
    open(idx, "a").write("\nHand-written preface.\n")
    wf = os.path.join(repo, ".github", "workflows", "publish-guide.yml")
    assert not os.path.exists(wf)
    st = site.status(proj)
    assert any("restores the missing Pages workflow" in n for n in st["next"])
    site.init(proj, apply=True)
    assert "Hand-written preface." in open(idx).read() and os.path.exists(os.path.join(gd, "about.qmd"))
    assert os.path.exists(wf), "init repairs the workflow"


def test_new_projects_get_the_website_pages_by_default(proj):
    from research import guide
    guide.init(proj)
    assert os.path.exists(os.path.join(PV.guide_dir(proj), "research-question.qmd"))


# ----------------------------------------------------------------------------------------------- GitHub Actions
def test_one_canonical_pages_workflow_that_never_runs_code_and_deploys_the_default_branch(proj, repo):
    site.init(proj, apply=True)
    wf = open(os.path.join(repo, ".github", "workflows", "publish-guide.yml")).read()
    rel = os.path.relpath(PV.guide_dir(proj), repo)
    assert re.search(r"push:\s*\n\s*branches: \[main\]", wf) and "workflow_dispatch:" in wf
    assert "--no-execute" in wf and "quarto-dev/quarto-actions/setup" in wf and "actions/configure-pages" in wf
    assert "actions/upload-pages-artifact" in wf and "actions/deploy-pages" in wf and f"path: {rel}/_output/scientific" in wf
    assert "pages: write" in wf and "id-token: write" in wf and "python" not in wf.lower() and "pip " not in wf
    wfs = [f for f in os.listdir(os.path.join(repo, ".github", "workflows"))]
    assert wfs == ["publish-guide.yml"]
    # a second deploying workflow is reported (they would fight over Pages)
    other = os.path.join(repo, ".github", "workflows", "other.yml")
    open(other, "w").write("jobs:\n  d:\n    steps:\n      - uses: actions/deploy-pages@v4\n")
    assert any("Several workflows deploy" in n for n in site.status(proj)["next"])


def test_workflow_follows_a_non_main_default_branch(tmp_path, monkeypatch):
    from research.store import Project
    d = tmp_path / "trunk"
    d.mkdir()
    git(d, "init", "-q", "-b", "trunk")
    (d / "a.py").write_text("x=1\n")
    git(d, "add", ".")
    git(d, "commit", "-qm", "i")
    monkeypatch.chdir(d)
    p = Project.init(str(d), name="t", goal="g")
    site.init(p, apply=True)
    assert "branches: [trunk]" in open(d / ".github" / "workflows" / "publish-guide.yml").read()
    p.close()


# ----------------------------------------------------------------------------------------------- status and publishing instructions
def test_status_gives_exact_next_commands_and_never_pushes(proj, repo):
    st = site.status(proj)
    assert not st["guide"] and any("research site init --apply" in n for n in st["next"])
    site.init(proj, apply=True)
    st = site.status(proj)
    assert st["pushes_automatically"] is False and any("No remote yet" in n for n in st["next"]) and any("git" in n and "commit" in n for n in st["next"])
    git(repo, "remote", "add", "origin", "git@github.com:alice/diffusion-study.git")
    st = site.status(proj)
    assert st["remote"]["pages_settings"] == "https://github.com/alice/diffusion-study/settings/pages"
    assert st["remote"]["site_url"] == "https://alice.github.io/diffusion-study/"
    assert any("git push origin main" in n for n in st["next"]) and any("Source: GitHub Actions" in n for n in st["next"])
    git(repo, "remote", "set-url", "origin", "https://github.com/alice/alice.github.io.git")
    assert site.status(proj)["remote"]["site_url"] == "https://alice.github.io/", "a user site lives at the root, not under /<repo>/"
    refs = subprocess.run(["git", "ls-remote", "--heads", "."], cwd=repo, capture_output=True, text=True).stdout
    assert "site" not in refs


def test_preview_command_is_local_first_and_explains_ssh_port_forwarding(proj, monkeypatch):
    site.init(proj, apply=True)
    monkeypatch.setattr(GQ, "find_quarto", lambda p=None: "/usr/bin/quarto")
    monkeypatch.setattr(GQ, "doctor", lambda p=None: {"ok": True, "quarto": "/usr/bin/quarto", "problems": [], "hints": [], "pdf": False})
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    r = site.preview_command(proj, port=4321, browse=True)
    assert r["argv"][1] == "preview" and "--no-execute" in r["argv"] and r["argv"][-4:] == ["--port", "4321", "--host", "127.0.0.1"] and r["url"] == "http://localhost:4321/"
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5 6.7.8.9 22")
    r = site.preview_command(proj, port=4321)
    assert "--no-browser" in r["argv"] and any("ssh -L 4321:localhost:4321" in n for n in r["notes"])


# ----------------------------------------------------------------------------------------------- publishing an experiment
def test_add_requires_a_verified_experiment_and_fails_closed(proj, repo, exp):
    site.init(proj, apply=True)
    with pytest.raises(ResearchError, match="not verified yet"):
        site.add(proj, exp)                                       # no tasks, nothing verified: never published silently
    assert not os.path.exists(os.path.join(PV.guide_dir(proj), "experiments"))


def test_add_publishes_only_committed_safe_report_files_and_writes_an_honest_draft(proj, repo, exp):
    site.init(proj, apply=True)
    sha = commit_reports(repo, {
        "reports/.env": "TOKEN=abc", "reports/notes/api_token.txt": "x", "reports/hpc/job.csv": "a,b\n", "reports/logs/run.log": "log",
        "reports/quarto/chapters/secret.qmd": "# s\n\nkey: ghp_" + "a" * 30 + "\n", "reports/big.png": "x" * 6_000_000,
        "reports/ledger/q.qmd": "# internal\n", "reports/run.ipynb": "{}", "src/not_a_report.qmd": "# no\n", "reports/data.pkl": "p"})
    (repo / "reports" / "quarto" / "chapters" / "uncommitted.qmd").write_text("# never committed\n")
    r = site.add(proj, exp, at=sha)
    gd = PV.guide_dir(proj)
    base = os.path.join(gd, "experiments", "diffusion-smoke-test")
    got = {os.path.relpath(os.path.join(d, f), base) for d, _, fs in os.walk(base) for f in fs}
    assert got == {"index.qmd", "reports/quarto/chapters/00-smoke.qmd", "reports/quarto/figures/profile.png", "reports/quarto/figures/table.csv"}
    skipped = {s["path"]: s["why"] for s in r["skipped"]}
    assert {"reports/.env", "reports/notes/api_token.txt", "reports/hpc/job.csv", "reports/logs/run.log", "reports/ledger/q.qmd", "reports/run.ipynb", "reports/big.png",
            "reports/quarto/chapters/secret.qmd"} <= set(skipped) and "src/not_a_report.qmd" not in got
    assert "credential" in skipped["reports/quarto/chapters/secret.qmd"] and "larger" in skipped["reports/big.png"]
    pub = open(os.path.join(base, "reports/quarto/chapters/00-smoke.qmd")).read()
    assert "```{python}" not in pub and "```python" in pub and any("not executed" in w for w in r["warnings"]), "a cell never runs on the website"
    page = open(os.path.join(base, "index.qmd")).read()
    assert "The width grows like sqrt(t)." in page and "Explicit finite differences on a 1-D grid." in page and "TODO: what this experiment cannot show" in page
    assert page.count("_TODO") >= 3 and "Evidence still missing" in page and "no citation" not in page.lower()
    assert "[@" not in page and "doi" not in page.lower(), "no invented citations"
    assert {f"experiments/diffusion-smoke-test/index.qmd", "experiments/diffusion-smoke-test/reports/quarto/chapters/00-smoke.qmd"} <= set(GQ.listed_chapters(proj))
    assert not os.path.exists(os.path.join(gd, "experiments", "diffusion-smoke-test", "reports", "quarto", "chapters", "uncommitted.qmd"))


def test_add_is_idempotent_and_never_overwrites_your_edits(proj, repo, exp):
    site.init(proj, apply=True)
    sha = commit_reports(repo)
    site.add(proj, exp, at=sha)
    base = os.path.join(PV.guide_dir(proj), "experiments", "diffusion-smoke-test")
    snap = snapshot(PV.guide_dir(proj))
    again = site.add(proj, exp, at=sha)
    assert again["written"] == [] and again["refreshed"] == [] and snapshot(PV.guide_dir(proj)) == snap
    # you rewrite the draft page and the chapter; the source then changes in a newer commit
    open(os.path.join(base, "index.qmd"), "w").write("# My title\n\nMy words.\n")
    ch = os.path.join(base, "reports/quarto/chapters/00-smoke.qmd")
    open(ch, "a").write("\nMy remark.\n")
    sha2 = commit_reports(repo, {"reports/quarto/chapters/00-smoke.qmd": QMD + "\nUpstream addition.\n", "reports/quarto/figures/profile.png": "NEWPNG"})
    r = site.add(proj, exp, at=sha2)
    assert open(os.path.join(base, "index.qmd")).read() == "# My title\n\nMy words.\n"
    assert "My remark." in open(ch).read() and "Upstream addition" not in open(ch).read() and "reports/quarto/chapters/00-smoke.qmd" in r["kept_yours"]
    assert any("you edited it" in w for w in r["warnings"])
    assert open(os.path.join(base, "reports/quarto/figures/profile.png")).read() == "NEWPNG", "an untouched generated file follows the source"
    assert sorted(site.status(proj)["experiments"][0]["edited_by_you"]) == ["index.qmd", "reports/quarto/chapters/00-smoke.qmd"]


def test_remove_previews_then_unpublishes_without_deleting_and_add_restores(proj, repo, exp):
    site.init(proj, apply=True)
    site.add(proj, exp, at=commit_reports(repo))
    gd = PV.guide_dir(proj)
    base = os.path.join(gd, "experiments", "diffusion-smoke-test")
    open(os.path.join(base, "index.qmd"), "a").write("\nMy analysis.\n")
    snap = snapshot(gd)
    r = site.remove(proj, exp)
    assert r["dry_run"] and snapshot(gd) == snap and r["edited_by_you"] == ["index.qmd"]
    r = site.remove(proj, exp, apply=True)
    assert not r["dry_run"] and not os.path.exists(base)
    kept = os.path.join(gd, "_unpublished", "diffusion-smoke-test")
    assert "My analysis." in open(os.path.join(kept, "index.qmd")).read(), "your edits are kept, not deleted"
    assert not [c for c in GQ.listed_chapters(proj) if c.startswith("experiments/")] and "about.qmd" in GQ.listed_chapters(proj)
    assert site.status(proj)["experiments"][0]["status"] == "unpublished" and site.remove(proj, exp, apply=True)["already"]
    assert site.sync(proj)["added"] == [], "automatic inclusion never re-publishes what you unpublished"
    r = site.add(proj, exp)                                      # restore needs no verification: the content was already published
    assert r["restored"] and "My analysis." in open(os.path.join(base, "index.qmd")).read()
    assert f"experiments/diffusion-smoke-test/index.qmd" in GQ.listed_chapters(proj)


def test_slugs_hide_experiment_ids_and_stay_unique(proj, repo):
    site.init(proj, apply=True)
    a = services.create_experiment(proj, "Same title")["id"]
    b = services.create_experiment(proj, "Same title", override="test")["id"]
    sha = commit_reports(repo)
    assert site.add(proj, a, at=sha)["slug"] == "same-title" and site.add(proj, b, at=sha)["slug"] == "same-title-2"


# ----------------------------------------------------------------------------------------------- link check
def test_link_checker_finds_missing_local_targets_only(tmp_path):
    (tmp_path / "a.html").write_text('<a href="b.html">b</a><img src="fig.png"><a href="https://x.org">x</a><a href="#top">t</a><a href="gone.html#s">g</a>')
    (tmp_path / "b.html").write_text("ok")
    assert site.check_links(str(tmp_path)) == [{"page": "a.html", "link": "fig.png"}, {"page": "a.html", "link": "gone.html#s"}]


@needs_quarto
def test_build_renders_the_site_offline_and_checks_links(proj, repo, exp):
    site.init(proj, apply=True)
    r = site.build(proj)
    assert r["ok"] and r["links"] == []
    out = os.path.join(PV.guide_dir(proj), "_output", "scientific")
    assert os.path.exists(os.path.join(out, "index.html")) and "Understand alpha" in open(os.path.join(out, "about.html")).read()


def test_refreshing_generated_chapters_keeps_the_draft_page_metadata_consistent(proj, repo, exp):
    site.init(proj, apply=True)
    sha = commit_reports(repo)
    site.add(proj, exp, at=sha)
    base = os.path.join(PV.guide_dir(proj), "experiments", "diffusion-smoke-test")
    page = os.path.join(base, "index.qmd")
    assert sha[:10] in open(page).read()
    sha2 = commit_reports(repo, {"reports/quarto/chapters/00-smoke.qmd": QMD + "\nUpstream addition.\n"})
    r = site.add(proj, exp, at=sha2)
    text = open(page).read()
    assert "index.qmd" in r["refreshed"] and sha2[:10] in text and sha[:10] not in text            # an untouched page follows the chapters it lists
    assert site.status(proj)["experiments"][0]["commit"][:10] == sha2[:10] if "commit" in site.status(proj)["experiments"][0] else True
    open(page, "w").write("# My title\n\nMy words.\n")                                              # once you edit it, it is never rewritten, and you are told
    sha3 = commit_reports(repo, {"reports/quarto/chapters/00-smoke.qmd": QMD + "\nUpstream addition 2.\n"})
    r = site.add(proj, exp, at=sha3)
    assert open(page).read() == "# My title\n\nMy words.\n" and any("may show an older commit" in w for w in r["warnings"])
