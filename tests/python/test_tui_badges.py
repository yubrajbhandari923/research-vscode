"""Every status the core can store gets a colour in the TUI, so a new status cannot silently render as plain text."""
import pytest

pytest.importorskip("textual")


def test_every_core_status_has_a_badge_colour():
    from research.schema import REVIEW_VERDICTS, STATUSES
    from research_cli.tui.widgets import STATUS
    known = {s for names in STATUS.values() for s in names.split()}
    wanted = {s for vals in STATUSES.values() for s in vals} | {v for vals in REVIEW_VERDICTS.values() for v in vals}
    assert not wanted - known, sorted(wanted - known)


def test_record_detail_is_readable_not_a_dict_dump():
    from research_cli.tui.widgets import render
    plan = {"id": "PLAN-001", "title": "Compare priors", "status": "active", "author_type": "human", "author_name": "demo",
            "created_at": "2026-11-08T09:32:35-05:00", "updated_at": "2026-11-08T09:32:35-05:00", "objective": "Decide TV vs learned prior",
            "note": None, "parameters": {"init": "fbp", "seed": 1},
            "tasks": [{"id": "T-001", "type": "task", "plan_id": "PLAN-001", "title": "Implement learned prior", "status": "done", "claimed_by": None},
                      {"id": "T-002", "title": "Run low-dose comparison", "status": "running"}]}
    text = render(plan).plain
    assert "'type'" not in text and "plan_id" not in text and "None" not in text      # no raw dict/None noise
    assert "author_type" not in text and "created_at" not in text                      # boilerplate folded into one quiet line
    assert "demo (human)" in text and "created " in text and "T09:32" not in text      # provenance line, formatted times
    assert "T-001" in text and "Implement learned prior" in text and "T-002" in text   # tasks one per line
    assert "init=fbp  seed=1" in text                                                  # nested dict compact


def test_show_detail_before_any_screen_exists_is_a_noop():
    # a row-highlight event can arrive at start-up or shutdown, before/after any screen or pane exists: it must not crash the app
    from research_cli.tui import ResearchTUI
    app = ResearchTUI("/nonexistent-project")
    assert app.show_detail("records", "EXP-001") is None
    assert app.show_detail("runs", "RUN-0001") is None
