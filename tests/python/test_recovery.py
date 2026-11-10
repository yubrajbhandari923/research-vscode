"""Supervised failure recovery: a Slurm job that finished before its results were collected and ingested (real supervisor, scripted agent, no live model).

The scenario every test shares: task `job` runs an HPC job (one completed Slurm run, whose results are NOT collected yet) and is checked on a metric;
task `report` depends on it. No second HPC run may ever be created."""
import json
import sys
import textwrap
import time

import pytest

from research import planspec as PS, recovery as RC, runs as R, supervisor as SV
from research.hpc import remote as HR
from test_supervisor import lproj, phases, run_until  # noqa: F401  (fixtures and helpers of the supervisor suite)

RUN_PY = ("import os; from research.store import Project; from research import runs; p=Project(os.environ['RESEARCH_ROOT']); "
          "e=p.get('task',os.environ['RESEARCH_TASK'])['related_experiment_id']; "
          "r=runs.create_run(p,e,command='job',backend='slurm',status='completed',exit_code=0,slurm_job_id='9001'); "
          "runs._update_run_row(p,r['id'],{'slurm':{'hpc':{'state':'submitted','cluster':'c'}}})")


def plan(extra_exec="", on_failure=None, check="metric:loss<=0.5"):
    of = f", on_failure: {on_failure}" if on_failure else ""
    return textwrap.dedent(f"""\
        schema_version: 1
        plans:
          - key: race
            title: Race
            execution: {{max_attempts_per_task: 3, max_hpc_resubmissions: 0{of}{extra_exec}}}
            experiments:
              - key: e
                title: E
                tasks:
                  - key: job
                    title: Job
                    agent: {{role: implementer, profile: fake}}
                    resources: {{backend: slurm}}
                    checks: ["{check}"]
                    prompt: |
                      Submit.
                      MOCK_SH: {sys.executable} -c "{RUN_PY}"
                  - key: report
                    title: Report
                    depends_on: [job]
                    agent: {{role: implementer, profile: fake}}
                    checks: ["file:report.txt"]
                    prompt: |
                      Write the report.
                      MOCK_SH: echo done > report.txt
        """)


@pytest.fixture
def quiet_cluster(monkeypatch):
    """No cluster in these tests: the poll/collect step must not be what rescues the run (the test plays the part of the late collection)."""
    calls = []
    monkeypatch.setattr(HR, "sync", lambda *a, **k: calls.append(1) or {})
    return calls


def late_results(p, value=0.2):
    """What `research hpc collect` does once it finishes: ingest the metrics, then mark the run collected."""
    rid = p.q1("SELECT id FROM runs WHERE backend='slurm'")["id"]
    R.log_metric(p, "loss", value, run=rid)
    HR._set_hpc_state(p, rid, state="collected", collected_at="2026-01-01T00:00:00+00:00")
    return rid


def submitted(p):
    """The scripted agent registers the run and THEN records its Slurm state: act only once both steps are done."""
    row = p.q1("SELECT id FROM runs WHERE backend='slurm'")
    return bool(row and HR._hpc_state(R.get_run(p, row["id"])).get("state") == "submitted")


def entry(p, tid_key="job"):
    st = PS.load_state(p, "race")
    rs = SV.load_run(p, st["plan_id"])
    return rs, rs["tasks"][st["tasks"][tid_key]]


def slurm_runs(p):
    return [r["id"] for r in p.q("SELECT id FROM runs WHERE backend='slurm'")]


def test_verification_waits_for_collection_then_completes_and_unblocks_the_downstream_task(lproj, quiet_cluster):
    PS.apply(lproj, plan())
    SV.plan_run(lproj, "race", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj, "race")["job"] == "waiting_hpc", 60)
    for _ in range(3):                                         # Slurm says completed, the metric is not home: still waiting, never a failure
        SV.tick(lproj)
        time.sleep(0.3)
    rs, e = entry(lproj)
    assert e["phase"] == "waiting_hpc" and "not collected and ingested" in e["reason"] and e["recovery"]["cause"] == "collection_pending"
    assert len(e["attempts"]) == 1 and not e["attempts"][0].get("verification")      # nothing was judged
    assert phases(lproj, "race")["report"] != "running"
    late_results(lproj)
    assert run_until(lproj, lambda: SV.status(lproj, "race")["state"] == "completed", 90)
    rs, e = entry(lproj)
    assert e["phase"] == "verified" and len(e["attempts"]) == 1                      # the same attempt, no repair
    assert phases(lproj, "race") == {"job": "verified", "report": "verified"}
    assert len(slurm_runs(lproj)) == 1                                               # no new HPC submission


def test_an_overdue_collection_is_evidence_pending_not_a_repair_and_the_same_attempt_is_reverified_when_results_arrive(lproj, quiet_cluster):
    lproj.config["supervisor"]["recovery"] = {"collection_wait_minutes": 0}
    lproj.save_config()
    PS.apply(lproj, plan())
    SV.plan_run(lproj, "race", start_daemon=False)
    assert run_until(lproj, lambda: phases(lproj, "race")["job"] == "blocked", 60)
    rs, e = entry(lproj)
    a = e["attempts"][0]
    assert a["category"] == "evidence_pending" and len(e["attempts"]) == 1           # not an implementation failure: no repair attempt
    assert e["recovery"]["cause"] == "collection_pending" and "research hpc collect" in " ".join(e["recovery"]["needs_human"])
    assert "re-verified automatically" in e["reason"]
    assert "evidence_fp" in a["verification"] and not a["verification"]["passed"]
    first = dict(a["verification"])
    for _ in range(3):                                                               # same evidence: nothing is re-run, nothing changes
        SV.tick(lproj)
    assert entry(lproj)[1]["attempts"][0].get("reverifications", 0) == 0
    late_results(lproj)
    assert run_until(lproj, lambda: SV.status(lproj, "race")["state"] == "completed", 90)
    rs, e = entry(lproj)
    a = e["attempts"][0]
    assert a["reverifications"] == 1 and a["verification"]["passed"]
    assert a["verification_history"][0]["ts"] == first["ts"] and not a["verification_history"][0]["passed"]    # the earlier result is preserved
    assert e["recovery"]["decision"].startswith("recovered automatically")
    assert [x["action"] for x in rs["recovery_log"] if x["task"] == next(iter(rs["tasks"]))][-2:] == ["reverify", "recovered"]
    assert len(slurm_runs(lproj)) == 1 and len(e["attempts"]) == 1


def test_a_paused_plan_reverifies_new_evidence_resumes_and_runs_the_downstream_task(lproj, quiet_cluster):
    """on_failure=pause: the missing metric (results collected, metric not logged yet) pauses the plan; the late metric heals it without a human."""
    PS.apply(lproj, plan(on_failure="pause"))
    SV.plan_run(lproj, "race", start_daemon=False)
    rid_box = {}

    def collected_without_metric():
        if not rid_box and submitted(lproj):
            rid_box["id"] = lproj.q1("SELECT id FROM runs WHERE backend='slurm'")["id"]
            HR._set_hpc_state(lproj, rid_box["id"], state="collected")
        return SV.status(lproj, "race")["state"] == "paused"
    assert run_until(lproj, collected_without_metric, 90)
    rs, e = entry(lproj)
    assert rs["state"] == "paused" and e["phase"] == "attention" and rs["paused_for"] == next(iter(rs["tasks"]))
    h = SV.plan_health(lproj, PS.load_state(lproj, "race"), rs)
    assert h["state"] == "paused" and [b["task"] for b in h["stuck"]] == [next(iter(rs["tasks"]))]       # a paused plan is diagnosable like a halted one
    assert h["stuck"][0]["recovery"]["cause"] == "late_metric"
    assert phases(lproj, "race")["report"] != "verified"
    R.log_metric(lproj, "loss", 0.1, run=rid_box["id"])                              # the metric is ingested late
    assert run_until(lproj, lambda: SV.status(lproj, "race")["state"] == "completed", 90)
    assert len(slurm_runs(lproj)) == 1


def test_a_numerical_miss_is_never_reverified_into_a_pass_or_relaxed(lproj, quiet_cluster):
    """Complete evidence that misses the criterion is a scientific outcome: the supervisor explains it, keeps the check, and asks for a person."""
    PS.apply(lproj, plan(on_failure="pause"))
    SV.plan_run(lproj, "race", start_daemon=False)
    box = {}

    def poisoned():
        if not box and submitted(lproj):
            box["id"] = lproj.q1("SELECT id FROM runs WHERE backend='slurm'")["id"]
            R.log_metric(lproj, "loss", 0.9, run=box["id"])
            HR._set_hpc_state(lproj, box["id"], state="collected")
        return SV.status(lproj, "race")["state"] == "paused"
    assert run_until(lproj, poisoned, 90)
    rs, e = entry(lproj)
    r = e["recovery"]
    assert r["cause"] == "numerical" and "never relaxes a threshold" in " ".join(r["needs_human"]) and r["decision"].startswith("no automatic recovery")
    contract = lproj.get("task", next(iter(rs["tasks"])))["checks"]
    for _ in range(4):
        SV.tick(lproj)
    rs, e = entry(lproj)
    assert e["attempts"][0].get("reverifications", 0) == 0 and rs["state"] == "paused"
    assert lproj.get("task", next(iter(rs["tasks"])))["checks"] == contract
    assert len(slurm_runs(lproj)) == 1


def test_recovery_can_be_switched_off(lproj, quiet_cluster):
    lproj.config["supervisor"]["recovery"] = {"enabled": False, "collection_wait_minutes": 0}
    lproj.save_config()
    PS.apply(lproj, plan(on_failure="pause"))
    SV.plan_run(lproj, "race", start_daemon=False)
    assert run_until(lproj, lambda: SV.status(lproj, "race")["state"] == "paused", 60)
    late_results(lproj)
    for _ in range(4):
        SV.tick(lproj)
    assert SV.status(lproj, "race")["state"] == "paused" and entry(lproj)[1]["attempts"][0].get("reverifications", 0) == 0


# ------------------------------------------------------------------------------------------------ surfaces and bounded diagnosis
def paused_with_numerical_miss(lproj):
    PS.apply(lproj, plan(on_failure="pause"))
    SV.plan_run(lproj, "race", start_daemon=False)
    box = {}

    def poisoned():
        if not box and submitted(lproj):
            box["id"] = lproj.q1("SELECT id FROM runs WHERE backend='slurm'")["id"]
            R.log_metric(lproj, "loss", 0.9, run=box["id"])
            HR._set_hpc_state(lproj, box["id"], state="collected")
        return SV.status(lproj, "race")["state"] == "paused"
    assert run_until(lproj, poisoned, 90)
    return next(iter(entry(lproj)[0]["tasks"]))


def test_the_root_cause_and_what_needs_a_person_reach_status_chat_halt_text_and_the_inbox(lproj, quiet_cluster):
    from research import notify
    from research.api import chat as CH
    tid = paused_with_numerical_miss(lproj)
    row = next(t for t in SV.status(lproj, "race")["tasks"] if t["id"] == tid)
    assert row["recovery"]["cause"] == "numerical"
    stuck = notify._blocked(lproj)                                                   # a paused plan's failed task is listed like a halted plan's
    assert [b["task"] for b in stuck] == [tid] and stuck[0]["recovery"]["cause"] == "numerical"
    halt = {"plan": "race", "paused": True, "blocking": stuck}
    msg = notify.message_for({"state": "none"}, halt)
    assert "PAUSED with failed tasks" in msg and "Cause:" in msg and "Needs you:" in msg and "never relaxes a threshold" in msg
    tasks = CH.chat_cmd_tasks(lproj)["text"] if isinstance(CH.chat_cmd_tasks(lproj), dict) else str(CH.chat_cmd_tasks(lproj))
    assert "Cause:" in tasks and "Decision:" in tasks
    from research_cli.commands import supervisor as CLS
    assert any("Cause:" in l for l in CLS._status_lines(SV.status(lproj, "race")))
    inbox = pytest.importorskip("research_cli.tui.inbox")
    items = []
    inbox._blocked(lproj, items)
    assert "Needs you:" in items[0]["why"] and "never relaxes" in items[0]["next"]


def test_automatic_diagnosis_may_only_reverify_note_or_guide_by_itself(lproj, quiet_cluster):
    from research import intervene as I, investigator as INV
    tid = paused_with_numerical_miss(lproj)
    ctx = I.gather(lproj, tid)
    assert ctx["recovery"]["cause"] == "numerical" and ctx["evidence"]["runs"][0]["metrics"]["loss"] == 0.9 and ctx["evidence"]["runs"][0]["stage"] == "collected"

    def apply(actions, safe_only=True):
        entry_ = {}
        for it in I.list_all(lproj, open_only=True):                          # one open intervention per task: clear the previous one
            I.cancel(lproj, it["id"])
        return INV._apply(lproj, tid, ctx, {"summary": "s", "rationale": "r", "actions": actions}, "diagnose", None, "investigator auto", entry_, safe_only), entry_

    out, e = apply([{"type": "note", "text": "look at the learning rate"}])
    assert e["interventions"] and "Waiting for your PIN" not in out["summary"]                     # safe: done by itself
    out, e = apply([{"type": "retry"}])
    assert "Waiting for your PIN" in out["summary"] or "needs your approval" in out["summary"].lower() or "Waiting for" in out["summary"]
    out, e = apply([{"type": "reverify"}, {"type": "retry"}])
    assert "Waiting for" in out["summary"]                                                         # a resume travels with the held part
    out, e = apply([{"type": "set_resources", "fields": {"time": "99:00:00"}}], safe_only=False)
    assert "Waiting for" in out["summary"]                                                         # protected whatever the mode
    assert "reverify" in INV.SAFE_AUTO and "retry" not in INV.SAFE_AUTO and "set_resources" not in INV.SAFE_AUTO
