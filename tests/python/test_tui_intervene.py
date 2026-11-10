"""The TUI's Intervene menu: the same deterministic controls as `research task intervene`, previewed before they apply, PIN only where a resource change needs it."""
import pytest

pytest.importorskip("textual")
from research_cli.tui import ResearchTUI
from research_cli.tui.widgets import ActionMenu, Confirm, Prompt
from research import takeover as TK
from test_supervisor import lproj  # noqa: F401  (fixture)
from test_takeover import PIN, stuck  # noqa: F401  (fixture)
from test_tui import go


def test_menu_preview_and_pin_gated_budget_change(stuck):
    p, tid = stuck
    app = ResearchTUI(p.root)

    async def then(pilot):
        app.intervene(tid)
        await pilot.pause()
        assert isinstance(app.screen, ActionMenu) and "EXHAUSTED" in app.screen.title_text and "attempts 1/1" in app.screen.title_text
        labels = " | ".join(o[1] for o in app.screen.options)
        assert "attempt budget is exhausted" in labels and "Take over" in labels, "unavailable actions are shown with the reason"
        app.screen.dismiss("attempts")
        await pilot.pause()
        assert isinstance(app.screen, Prompt)
        app.screen.dismiss("12")
        await pilot.pause()
        assert isinstance(app.screen, Confirm) and "1 -> 12" in app.screen.text and "nothing is reset or voided" in app.screen.text
        assert TK.menu(p, tid)["attempts"]["allowed"] == 1, "previewed, not applied"
        app.screen.dismiss(True)
        await pilot.pause()
        assert isinstance(app.screen, Prompt) and app.screen.password, "a resource change asks for the PIN (masked)"
        app.screen.dismiss(PIN)
        await pilot.pause(0.5)
        assert TK.menu(p, tid)["attempts"]["allowed"] == 12
    go(app, then)


def test_takeover_from_the_menu_needs_no_pin_and_unavailable_actions_only_explain(stuck):
    p, tid = stuck
    app = ResearchTUI(p.root)

    async def then(pilot):
        app.intervene(tid)
        await pilot.pause()
        app.screen.dismiss("retry")                               # exhausted: only a notification, no dialog
        await pilot.pause()
        assert not isinstance(app.screen, (Prompt, Confirm))
        app.intervene(tid)
        await pilot.pause()
        app.screen.dismiss("takeover")
        await pilot.pause()
        assert isinstance(app.screen, Confirm) and "will not be launched, repaired or escalated" in app.screen.text
        app.screen.dismiss(True)
        await pilot.pause(0.5)
        assert TK.menu(p, tid)["takeover"] and TK.menu(p, tid)["phase"] == "takeover"
    go(app, then)
