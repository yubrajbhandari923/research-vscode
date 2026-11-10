"""INT-003 (PRAVAH): an intervention added T-017, then failed on the next add_task because `agent.role` was invalid, leaving it terminally `failed` with its first
action applied. The whole intervention is now validated against the complete plan schema before anything is applied; a partly applied one can be diagnosed,
corrected (which asks for approval again) and retried without repeating what was applied. Real supervisor ticks, git and a scripted agent."""
import pytest

from research import approval as A, intervene as I, planspec as PS, supervisor as SV
from research.util import ResearchError
from test_intervention import WRONG, blocked, start, ticks  # noqa: F401
from test_supervisor import lproj, phase  # noqa: F401  (lproj is a fixture)

GOOD = {"key": "oracle-review", "title": "Oracle review", "prompt": "Review the oracle.", "agent": {"role": "verifier"}}
BAD = {"key": "e01-oracle-review", "title": "Oracle review 2", "prompt": "Review again.", "agent": {"role": "reviewer"}}     # not a role
FROZEN = {"type": "change_frozen_test", "path": "tests/test_oracle.py", "reason": "the oracle asserted the wrong sign"}
AMEND = {"type": "amend_task", "fields": {"acceptance": "The oracle output exists."}}


def keys(p):
    return sorted(PS.load_state(p, "fx")["tasks"])


@pytest.fixture
def stuck(lproj):
    A.set_pin("4321")
    start(lproj, WRONG)
    assert ticks(lproj, lambda: blocked(lproj, "w"))
    return lproj, phase(lproj, "w")["id"]


def test_a_bad_role_anywhere_in_the_proposal_is_refused_before_anything_exists(stuck):
    p, tid = stuck
    before = keys(p)
    with pytest.raises(ResearchError, match="agent.role must be one of planner, implementer, verifier, analyst, human"):
        I.create_planned(p, tid, "split the review", [{"type": "add_task", "task": GOOD}, FROZEN, {"type": "add_task", "task": BAD}])
    assert keys(p) == before and I.list_all(p) == [], "no intervention was stored, no task was added"


def test_the_plan_schema_is_checked_as_a_whole_not_per_action(stuck):
    """A well-formed role but an unknown field, or a missing dependency, fails the complete schema: still nothing is applied."""
    p, tid = stuck
    odd = {**GOOD, "key": "x1", "agent": {"role": "verifier", "colour": "red"}}
    with pytest.raises(ResearchError, match="nothing was changed"):
        I.create_planned(p, tid, "add", [{"type": "add_task", "task": GOOD}, {"type": "add_task", "task": odd}])
    dep = {**GOOD, "key": "x2", "depends_on": ["no-such-task"]}
    with pytest.raises(ResearchError, match="nothing was changed"):
        I.create_planned(p, tid, "add", [{"type": "add_task", "task": dep}])
    assert keys(p) == ["w"]


def test_a_valid_intervention_still_needs_one_pin_approval_for_protected_parts(stuck):
    p, tid = stuck
    it = I.create_planned(p, tid, "oracle review and a frozen-test fix", [{"type": "add_task", "task": GOOD}, FROZEN, AMEND])
    assert it["state"] == "proposed" and set(it["protected"]) == {"frozen_test", "scientific"}, "scientific and frozen-test changes keep waiting for a human"
    with pytest.raises(Exception):
        I.approve(p, it["id"], "ok", pin="0000")
    assert keys(p) == ["w"]


def partly_applied(p, tid):
    """The INT-003 ledger an older version left: action 1 applied, action 2 (invalid role) failed, state failed."""
    it = I.create_planned(p, tid, "oracle review", [{"type": "add_task", "task": GOOD}, {"type": "add_task", "task": {**GOOD, "key": "e01-oracle-review", "title": "Oracle review 2"}}, FROZEN])
    I.approve(p, it["id"], "agreed", pin="4321")
    it = I.load(p, it["id"])
    st = PS.load_state(p, "fx")
    rs = SV.load_run(p, st["plan_id"])
    with SV.human_grant("test"):
        I._apply(p, st, rs, it, 0, it["actions"][0], SV)
    it["applied"]["0"] = "2026-11-10T01:00:00-05:00"
    it["actions"][1]["task"]["agent"] = {"role": "reviewer"}                 # what the old validator let through
    I._set(it, "failed", error="Invalid research plan: task fx/e01-oracle-review.agent.role must be one of planner, implementer, verifier, analyst, human")
    I.save(p, it)
    return it["id"]


def test_diagnose_names_what_was_applied_and_what_cannot_be(stuck):
    p, tid = stuck
    iid = partly_applied(p, tid)
    d = I.diagnose(p, iid)
    assert d["applied"] == 1 and d["pending"] == 2 and not d["applicable_now"] and "agent.role" in d["problem"]
    assert [r["applied"] is not None for r in d["actions"]] == [True, False, False] and "edit" in d["next"]
    assert keys(p) == ["oracle-review", "w"], "the first added task exists exactly once"
    with pytest.raises(ResearchError, match="would fail again"):
        I.retry(p, iid)                                                      # retrying unchanged text is refused up front, not tried


def test_correct_the_bad_action_then_approve_and_retry_without_duplicating_the_applied_one(stuck):
    p, tid = stuck
    iid = partly_applied(p, tid)
    applied_at = I.load(p, iid)["applied"]["0"]
    n_before = len(keys(p))
    with pytest.raises(ResearchError, match="already applied"):
        I.edit_action(p, iid, 1, {"type": "add_task", "task": GOOD})          # history is never rewritten
    fixed = {"type": "add_task", "task": {**GOOD, "key": "e01-oracle-review", "title": "Oracle review 2", "agent": {"role": "verifier"}}}
    it = I.edit_action(p, iid, 2, fixed, "role was invalid")
    assert it["state"] == "proposed" and "frozen_test" in it["protected"] and "approval" not in it, "the earlier approval covered different text: approval is asked again"
    ticks(p, lambda: False, 3)
    assert len(keys(p)) == n_before, "nothing is applied while it waits"
    I.approve(p, iid, "corrected the role", pin="4321")
    assert ticks(p, lambda: I.load(p, iid)["state"] in ("verifying", "resolved"), 60)
    done = I.load(p, iid)
    assert set(done["applied"]) == {"0", "1", "2"} and done["applied"]["0"] == applied_at, "the first action keeps its original timestamp: it was not repeated"
    assert len(keys(p)) == n_before + 1 and "oracle-review" in PS.export(p, "fx") and PS.export(p, "fx").count("key: oracle-review") == 1
    assert any(h["state"] == "edited" for h in done["history"])


def test_retry_resumes_a_failed_approved_intervention_under_its_own_approval(stuck):
    p, tid = stuck
    iid = partly_applied(p, tid)
    it = I.load(p, iid)
    it["actions"][1]["task"]["agent"] = {"role": "verifier"}                 # the text is now valid (e.g. fixed by hand in the record); the approval covered the same intent
    I.save(p, it)
    I.retry(p, iid, "now valid")
    assert I.load(p, iid)["state"] == "approved" and I.load(p, iid)["retries"][0]["applied_before"] == 1
    assert ticks(p, lambda: set(I.load(p, iid)["applied"]) == {"0", "1", "2"}, 60)
    assert PS.export(p, "fx").count("key: oracle-review") == 1, "T-017 is not duplicated"


def test_add_task_is_idempotent_when_the_task_is_already_in_the_plan(stuck):
    p, tid = stuck
    it = I.create_planned(p, tid, "add once", [{"type": "add_task", "task": GOOD}])
    ticks(p, lambda: I.load(p, it["id"])["state"] in ("verifying", "resolved"), 60)
    n = len(keys(p))
    st, rs = PS.load_state(p, "fx"), SV.load_run(p, PS.load_state(p, "fx")["plan_id"])
    again = I.load(p, it["id"])
    again["applied"] = {}                                                    # a crash between PS.apply and the flag
    with SV.human_grant("test"):
        I._apply(p, st, rs, again, 0, again["actions"][0], SV)
    assert len(keys(p)) == n
    with pytest.raises(ResearchError, match="different task with that key"):
        I.preflight(p, tid, [{"type": "add_task", "task": {**GOOD, "title": "something else"}}])


def test_the_attempt_cap_of_ten_is_gone_and_the_plan_budget_is_the_limit(stuck):
    p, tid = stuck
    assert I.validate_proposal({"actions": [{"type": "set_max_attempts", "value": 25}]})["actions"][0]["value"] == 25
    with pytest.raises(ResearchError, match="positive integer"):
        I.validate_proposal({"actions": [{"type": "set_max_attempts", "value": 0}]})
    it = I.create_planned(p, tid, "twelve attempts", [{"type": "set_max_attempts", "value": 12}])
    assert it["state"] == "proposed" and it["protected"] == ["resource"], "still a protected resource change"
    with pytest.raises(ResearchError, match="exceeds the plan's max_total_attempts"):
        I.create_planned(p, tid, "too many", [{"type": "set_max_attempts", "value": 100000}], allow_open=True)
    ok = I.create_planned(p, tid, "raise both", [{"type": "set_plan_budget", "field": "max_total_attempts", "value": 200000}, {"type": "set_max_attempts", "value": 100000}], allow_open=True)
    assert ok["state"] == "proposed", "raising the plan budget in the same intervention makes the later action valid"
