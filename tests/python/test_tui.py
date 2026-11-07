import asyncio
import os

import pytest

pytest.importorskip("textual")
import research
from research import approval, runs as R, services, supervisor as SV
from research_cli.tui import BANNER, ResearchTUI

SCRATCH = "/private/tmp/claude-501/-Users-yubraj-Code-research-vscode/54462c94-483d-4112-a8cd-d2bbe675c5ca/scratchpad"
SMALL, BIG = (80, 24), (140, 40)


def go(app, then, size=BIG):
    async def main():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await then(pilot)
    asyncio.run(main())


def text(app, wid):
    return str(app.query_one(wid).render())


def exp_fields(title="Try it"):
    return {k: f"{title} {k}" for k in ("title", "hypothesis", "rationale", "method", "expected_outputs", "related")}


@pytest.fixture
def proj(tmp_path):
    p = research.init(str(tmp_path), name="Full")
    e = services.create_experiment(p, "E1", hypothesis="h")
    r = R.create_run(p, e["id"], command="echo hi", backend="local", status="running")
    yield p, r, str(tmp_path)
    p.close()


def test_no_project_screen(tmp_path):
    app = ResearchTUI(str(tmp_path))

    async def then(pilot):
        assert "research init" in text(app, "#noproj")
        await pilot.press("x")
    go(app, then)


def test_empty_project_all_tabs_and_help(tmp_path):
    research.init(str(tmp_path), name="Empty").close()
    app = ResearchTUI(str(tmp_path))

    async def then(pilot):
        assert "Empty" in text(app, "#p-sys")
        assert "No runs yet" in text(app, "#p-status")
        for k in "123456":
            await pilot.press(k)
        assert "research core" in text(app, "#doctor")
        await pilot.press("question_mark")
        assert app.screen.__class__.__name__ == "Help"
        await pilot.press("escape", "q")
    go(app, then)


@pytest.mark.parametrize("size", [SMALL, BIG])
def test_banner_collapses_and_screenshot(proj, size):
    _, r, root = proj
    app = ResearchTUI(root)

    async def then(pilot):
        svg = pilot.app.export_screenshot()
        big = size == BIG
        assert big or "RESEARCHOS" in text(app, "#banner")  # compact one-line header (gradient splits the svg glyphs)
        assert app.query_one("#banner").region.height == (len(BANNER) + 1 if big else 1)
        assert app.query_one("#tabs").region.height > 8  # content is not squeezed
        assert "running" in text(app, "#p-status") and r["id"] in text(app, "#p-runs")
        os.makedirs(SCRATCH, exist_ok=True)
        open(os.path.join(SCRATCH, f"tui-dashboard-{size[0]}x{size[1]}.svg"), "w").write(svg)
        if big:
            open(os.path.join(SCRATCH, "tui-dashboard.svg"), "w").write(svg)
    go(app, then, size)


def test_runs_filter_and_kill_confirm(proj):
    _, r, root = proj
    app = ResearchTUI(root)

    async def then(pilot):
        assert app.query_one("#runs").row_count == 1
        await pilot.press("2", "enter")
        assert r["id"] in text(app, "#runs-d")
        await pilot.press("slash", "z", "z", "enter")
        assert app.query_one("#runs").row_count == 0
        await pilot.press("slash", "enter")
        assert app.query_one("#runs").row_count == 1
        await pilot.press("k")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Confirm"
        await pilot.press("n")
        assert app.srv.m_show(r["id"])["status"] == "running"
    go(app, then)


def _decide(proj, key, pin_set, monkeypatch):
    p, _, root = proj
    if pin_set:
        approval.set_pin("4242")
    pr = SV.propose_experiment(p, exp_fields())
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("4", "a" if key == "a" else "d")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Confirm"
        await pilot.press("y")
        await pilot.pause()
        if key == "a":
            await pilot.press(*"because", "enter")      # rationale
            await pilot.pause()
            if pin_set:
                assert app.screen.query_one("Input").password
                await pilot.press(*"4242", "enter")
                await pilot.pause()
        assert "4242" not in pilot.app.export_screenshot()
    go(app, then)
    return pr["id"], root


@pytest.mark.parametrize("pin_set", [False, True])
def test_approve_pending_proposal(proj, pin_set, monkeypatch):
    pid, root = _decide(proj, "a", pin_set, monkeypatch)
    from research.rpc import Server
    st = [x["status"] for x in Server(root).m_proposals() if x["id"] == pid][0]
    assert st in ("approved", "applied")


def test_deny_pending_proposal(proj, monkeypatch):
    pid, root = _decide(proj, "d", False, monkeypatch)
    from research.rpc import Server
    assert [x["status"] for x in Server(root).m_proposals() if x["id"] == pid][0] == "rejected"


def test_wrong_pin_does_not_approve(proj):
    p, _, root = proj
    approval.set_pin("4242")
    pr = SV.propose_experiment(p, exp_fields())
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("4", "a", "y")
        await pilot.pause()
        await pilot.press(*"why", "enter")
        await pilot.pause()
        await pilot.press(*"0000", "enter")
        await pilot.pause()
    go(app, then)
    from research.rpc import Server
    assert [x["status"] for x in Server(root).m_proposals() if x["id"] == pr["id"]][0] == "pending"
