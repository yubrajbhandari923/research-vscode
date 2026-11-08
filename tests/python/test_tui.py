import asyncio
import os

import pytest

pytest.importorskip("textual")
import research
from research import approval, decisions as D, runs as R, services, supervisor as SV, usage as U
from research_cli import tui as T
from research_cli.tui import BANNER, ResearchTUI
from research_cli.tui import views as V

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
        for k in "1234567":
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
        assert app.call("show", id=r["id"])["status"] == "running"
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
    from research import api
    st = [x["status"] for x in api.call("proposals", api.Session(root)) if x["id"] == pid][0]
    assert st in ("approved", "applied")


def test_deny_pending_proposal(proj, monkeypatch):
    pid, root = _decide(proj, "d", False, monkeypatch)
    from research import api
    assert [x["status"] for x in api.call("proposals", api.Session(root)) if x["id"] == pid][0] == "rejected"


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
    from research import api
    assert [x["status"] for x in api.call("proposals", api.Session(root)) if x["id"] == pr["id"]][0] == "pending"


# ----------------------------------------------------------------------------------------------- WP16: decisions, chat origin, status bar
OPTS = [{"key": "go", "label": "Retry with a hint", "consequence": "one more attempt", "action": None},
        {"key": "pay", "label": "Raise the budget", "consequence": "spends more", "protected": True, "action": None},
        {"key": "stop", "label": "Stop the plan", "consequence": "ends it", "action": {"kind": "op", "op": "plan_control", "params": {"plan": "nope", "action": "stop"}}}]


def ask(p, q="What now?", opts=OPTS):
    return D.request(p, q, opts, subject=None)["id"]


def shot(pilot, name, size):
    os.makedirs(SCRATCH, exist_ok=True)
    open(os.path.join(SCRATCH, f"tui-{name}-{size[0]}x{size[1]}.svg"), "w").write(pilot.app.export_screenshot())


def test_decisions_tab_lists_options_and_badges(proj):
    p, _, root = proj
    did = ask(p)
    app = ResearchTUI(root)

    async def then(pilot):
        assert "1 open decision" in text(app, "#status")
        await pilot.press("5")
        await pilot.pause()
        d = text(app, "#decisions-d")
        assert "What now?" in d and "Retry with a hint" in d and "one more attempt" in d and "needs your PIN" in d
        assert app.query_one("#decisions").row_count == 1 and "(1)" in str(app.query_one("#tabs").get_tab("t5").label)
        await pilot.press("question_mark")
        assert "Decisions: pick that option" in "".join(str(w.render()) for w in app.screen.query("Static"))
    go(app, then)
    assert D.load(p, did)["status"] == "open"


def test_pick_unprotected_option_runs_it(proj):
    p, _, root = proj
    did = ask(p)
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("5", "1")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Confirm"
        await pilot.press("y")
        await pilot.pause()
        assert "Noted" in "".join(n.message for n in app._notifications)
        assert "answered via the TUI" in text(app, "#decisions-d")
    go(app, then)
    a = D.load(p, did)["answer"]
    assert a["key"] == "go" and a["via"] == "tui"


def test_free_text_answer_goes_to_decision_answer(proj, monkeypatch):
    p, _, root = proj
    did = ask(p)
    seen = {}
    monkeypatch.setattr(D, "answer", lambda p, i, **kw: seen.update(id=i, **kw) or {"outcome": "Understood: skip it"})
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("5", "t")
        await pilot.press(*"skip it", "enter")
        await pilot.pause()
        await pilot.pause()
    go(app, then)
    assert seen["id"] == did and seen["text"] == "skip it" and seen["via"] == "tui" and seen["key"] is None


def _protected(proj, key_n, pin_set, typed, extra=()):
    p, _, root = proj
    if pin_set:
        approval.set_pin("4242")
    did = ask(p)
    app = ResearchTUI(root)
    shown = []

    async def then(pilot):
        await pilot.press("5", str(key_n), "y")
        await pilot.pause()
        await pilot.press(*"because", "enter")
        await pilot.pause()
        for step in extra + (typed,):
            if step is None:
                continue
            if step == "y":
                assert "No approval PIN is set" in str(app.screen.query_one("Label").render())
                await pilot.press("y")
            else:
                assert app.screen.query_one("Input").password
                await pilot.press(*step, "enter")
            await pilot.pause()
        shown.append(pilot.app.export_screenshot())
    go(app, then)
    assert [x for x in ("4242", "8317", "6203", "5519") if x in shown[0]] == []
    return D.load(p, did)


def test_protected_option_with_pin(proj):
    assert _protected(proj, 2, True, "4242")["status"] == "answered"


def test_protected_option_wrong_pin_stays_open(proj):
    assert _protected(proj, 3, True, "6203")["status"] == "open"


def test_protected_option_without_pin_offers_to_set_one(proj):
    d = _protected(proj, 2, False, "8317", extra=("y", "8317"))
    assert d["status"] == "answered" and approval.status()["set"]


def test_protected_option_without_pin_mismatch_sets_nothing(proj):
    d = _protected(proj, 2, False, "5519", extra=("y", "8317"))
    assert d["status"] == "open" and not approval.status()["set"]


def test_chat_requested_proposals_first_and_marked(proj):
    p, _, root = proj
    a, b, c = (SV.propose_experiment(p, exp_fields(f"P{i}")) for i in range(3))
    for pid, patch in ((b["id"], {"requested_from_chat": {"via": "chat:discord", "by": "kim", "ts": "2026-11-08T14:02:00+00:00"}}),
                       (c["id"], {"approved_via": {"method": "totp-chat", "platform": "telegram", "user_id": "7", "role": "approver"}, "status": "approved"})):
        pr = SV._prop_load(p, pid)
        pr.update(patch)
        SV._prop_save(p, pr)
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("4")
        t = app.query_one("#approvals")
        assert t.row_count == 3 and t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value == b["id"]
        assert "Discord: kim" in str(t.get_row_at(0)[3])
        app.show_detail("approvals", b["id"])
        assert "Requested from Discord by kim at" in text(app, "#approvals-d")
        app.show_detail("approvals", c["id"])
        assert "via chat (authenticator)" in text(app, "#approvals-d")
    go(app, then)


def test_answered_decision_says_who_and_where(proj):
    p, _, root = proj
    did = ask(p)
    D.answer(p, did, key="go", via="chat:discord")
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("5")
        await pilot.pause()
        assert "answered via Discord by" in text(app, "#decisions-d")
    go(app, then)


def test_older_core_without_decision_ops(proj, monkeypatch):
    from research import api
    monkeypatch.delitem(api.OPS, "decision_list")
    app = ResearchTUI(proj[2])

    async def then(pilot):
        await pilot.press("5")
        await pilot.pause()
        assert "no decision requests" in text(app, "#decisions-d") and "open decision" not in text(app, "#status")
        await pilot.press("t")
        assert not app.screen.is_modal
    go(app, then)


def test_status_bar_fields_usage_and_gateway(proj):
    p, _, root = proj
    U.record(p, "t", {"cost_usd": 1.5})
    app = ResearchTUI(root)

    async def then(pilot):
        s = text(app, "#status")
        assert "$1.50" in s and "0 agents running" in s and "chat off" in s
        assert "waiting for" not in s and "unattended" not in s
    go(app, then)


def sig(**kw):
    return {"running": 2, "unattended": [], "waiting": [], "spend": None, "decisions": 0, "gateway": {"known": True, "running": True, "platforms": ["telegram"], "paired": 1}, **kw}


def test_status_line_chips():
    plain_s = V.status_line(sig()).plain
    assert "2 agents running" in plain_s and "spend today n/a" in plain_s and "gateway" in plain_s and "telegram, 1 paired" in plain_s
    s = V.status_line(sig(unattended=["fx"], decisions=3, waiting=[{"provider": "claude", "until": "2099-01-01T15:05:00+00:00"}])).plain
    assert "unattended" in s and "3 open decisions" in s and "waiting for Claude until" in s and s.index("3 open") < s.index("agents running")


def test_plan_signals_reads_waiting_provider_defensively():
    st = {"concurrency": {"running": 1}, "auto_promotion": {"authorized": {"by": "me"}}, "waiting_provider": {"provider": "claude", "until": "x"}}
    calls = {"p1": st, "p2": {"concurrency": {"running": 2}}, "p3": {"waiting_provider": "garbage"}}

    def call(name, **kw):
        if name == "supervisor_status" and kw["plan"] == "p4":
            raise RuntimeError("older core")
        return calls[kw["plan"]]
    r = V.plan_signals(call, [{"key": k} for k in ("p1", "p2", "p3", "p4")])
    assert r["running"] == 3 and r["unattended"] == ["p1"] and r["waiting"] == [st["waiting_provider"]]


@pytest.mark.parametrize("size", [SMALL, BIG])
def test_every_tab_fits_both_sizes_and_screenshots(proj, size):
    p, _, root = proj
    ask(p)
    SV.propose_experiment(p, exp_fields())
    app = ResearchTUI(root)

    async def then(pilot):
        for k, name in (("1", "overview"), ("4", "approvals"), ("5", "decisions")):
            await pilot.press("left_square_bracket", "left_square_bracket", "left_square_bracket", "left_square_bracket", "left_square_bracket", "left_square_bracket")
            await pilot.press(k)
            await pilot.pause()
            assert app.query_one("#tabs").region.height > 8 and app.query_one("#status").region.height <= 2
            assert app.query_one("Footer").region.y + 1 == size[1]
            shot(pilot, name, size)
    go(app, then, size)


def test_no_color_has_no_glyphs_or_styles(proj, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    p, _, root = proj
    ask(p)
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("5")
        await pilot.pause()
        for wid in ("#status", "#decisions-d"):
            assert not any(ch in text(app, wid) for ch in "◆⚠⏳●○✓")
        assert app.has_class("plain") and app.theme == "ansi-dark" and "open decision" in text(app, "#status")
    go(app, then)
