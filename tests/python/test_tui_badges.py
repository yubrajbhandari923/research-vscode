"""Every status the core can store gets a colour in the TUI, so a new status cannot silently render as plain text."""
import pytest

pytest.importorskip("textual")


def test_every_core_status_has_a_badge_colour():
    from research.schema import REVIEW_VERDICTS, STATUSES
    from research_cli.tui.widgets import STATUS
    known = {s for names in STATUS.values() for s in names.split()}
    wanted = {s for vals in STATUSES.values() for s in vals} | {v for vals in REVIEW_VERDICTS.values() for v in vals}
    assert not wanted - known, sorted(wanted - known)
