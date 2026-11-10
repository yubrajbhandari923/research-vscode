"""The TUI: the standing-authorization and self-healing toggles are in Settings, and a settings profile is previewed, confirmed and applied (PIN masked) from the palette."""
import pytest

pytest.importorskip("textual")
from research import registry
from research_cli.tui import ResearchTUI
from research_cli.tui.widgets import ActionMenu, Confirm
from test_supervisor import proj  # noqa: F401  (fixture)
from test_tui import go

KEY = "auto_promotion.auto_approve_verified_code"


def test_the_toggles_are_listed_in_settings(proj):
    keys = {o["key"]: o for cat in ("git", "supervisor") for o in registry.describe(proj, cat)["options"]}
    assert keys[KEY]["type"] == "bool" and keys["supervisor_assistant.self_heal"]["label"] == "Automatically diagnose and repair recoverable failures"


def test_profile_menu_preview_and_apply(proj):
    app = ResearchTUI(proj.root)

    async def then(pilot):
        app.profile_prompt()
        await pilot.pause()
        assert isinstance(app.screen, ActionMenu) and any(o[0] == "overnight" for o in app.screen.options)
        app.screen.dismiss("overnight")
        await pilot.pause()
        assert isinstance(app.screen, Confirm) and "turns ON" in app.screen.text and "PIN" in app.screen.text
        assert registry.describe_value(proj, KEY) is False
        app.screen.dismiss(False)
        await pilot.pause()
        assert registry.describe_value(proj, KEY) is False, "declined: nothing applied"
    go(app, then)
