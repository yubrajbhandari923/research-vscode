import json

from research import guide as G
from research import services as S
from research.store import Project
from research_cli.main import main


def _syn(p, title="e"):
    e = p.insert_entity("experiment", {"title": title, "hypothesis": "h"}) if False else S.create_experiment(p, title)
    return S.synthesize(p, e["id"], what_happened="did it")


def test_agents_md_new_block_idempotent_and_preserves_user(repo):
    (repo / "AGENTS.md").write_text("# Mine\n\nKeep me.\n")
    p = Project.init(str(repo))
    p.write_agents_md()
    t = (repo / "AGENTS.md").read_text()
    assert t.startswith("# Mine\n\nKeep me.") and t.count("research:begin") == 1
    assert "Scientific practice" in t and "held-out" in t
    Project.init(str(repo)).write_agents_md()
    assert (repo / "AGENTS.md").read_text() == t


def test_empty_project_guide_bare_and_untouched(proj, repo):
    r = G.init_guide(proj)
    assert r["created"]
    path = repo / "docs" / "SCIENTIST_GUIDE.md"
    assert "No experiments have been synthesized" in path.read_text()
    path.write_text("mine\n" + path.read_text() + "user tail\n")
    before = path.read_text()
    assert not G.init_guide(proj)["created"] and path.read_text() == before
    assert G.status(proj)["up_to_date"]


def test_every_synthesis_pending_until_done(proj):
    G.init_guide(proj)
    s1, s2 = _syn(proj, "a"), _syn(proj, "b")
    assert [x["id"] for x in G.status(proj)["pending"]] == [s1["id"], s2["id"]]
    G.mark_done(proj, [s1["id"]])
    G.mark_done(proj, [s1["id"]])  # repeated: idempotent
    assert [x["id"] for x in G.status(proj)["pending"]] == [s2["id"]]
    s3 = _syn(proj, "c")  # synthesis during an earlier refresh is not lost
    G.mark_done(proj)
    assert G.status(proj)["up_to_date"] and s3["id"] in json.load(open(proj.path("guide.json")))["processed"]


def test_refresh_prepares_opus_and_marks_nothing(proj):
    s = _syn(proj)
    r = G.refresh(proj)  # "interrupted"/failed agent: nothing was marked
    assert r["model"] == "opus" and "opus" in r["argv"] and s["id"] in r["pending"]
    assert G.status(proj)["pending"] and not G.status(proj)["up_to_date"]
    assert G.refresh(proj)["pending"] == [s["id"]]  # retry safe
    G.mark_done(proj)
    assert G.refresh(proj)["up_to_date"]


def test_mark_done_rejects_unknown_and_missing_guide(proj):
    s = _syn(proj)
    try:
        G.mark_done(proj)
        assert False
    except Exception as e:
        assert "guide" in str(e)
    G.init_guide(proj)
    try:
        G.mark_done(proj, ["EXP-9/S9"])
        assert False
    except Exception as e:
        assert "Unknown" in str(e)


def test_status_reports_finding_statuses(proj):
    e = S.create_experiment(proj, "x")
    S.synthesize(proj, e["id"], what_happened="w")
    f = S.create_finding(proj, "claim", supports=[e["id"]])
    st = G.status(proj)["pending"][0]["findings"]
    assert st[0]["id"] == f["id"] and st[0]["status"] == "preliminary"


def test_cli_and_attention(proj, repo, capsys):
    assert main(["guide", "init"]) == 0
    _syn(proj)
    assert main(["guide", "status", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):])["pending"]
    from research import context as C
    assert any(a["kind"] == "guide_pending" for a in C.research_home(proj)["attention_items"])
    assert main(["guide", "done"]) == 0
    assert G.status(proj)["up_to_date"]
