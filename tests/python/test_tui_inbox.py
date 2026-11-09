"""The Needs-attention inbox, the experiment lifecycle explanation, artifact diagnostics, the guide and the synthesis actions, reproducing the
PRAVAH smoke test: one completed Slurm run whose two registered remote artifacts cannot be reached, one unsynthesized experiment, one planned
experiment and an empty Decisions tab."""
import os

import pytest

pytest.importorskip("textual")
import research
from research import api, decisions as D, runs as R, schema, services
from research.hpc import remote
from research_cli.tui import ResearchTUI, helptext, inbox as IN
from test_tui import BIG, SMALL, go, text


def call_for(root):
    s = api.Session(root)
    return s, (lambda name, **kw: api.call(name, s, kw, surface="tui"))


@pytest.fixture
def pravah(tmp_path):
    p = research.init(str(tmp_path), name="PRAVAH")
    planned = services.create_experiment(p, "Planned baseline", hypothesis="The baseline converges")["id"]
    hpc = services.create_experiment(p, "HPC convergence", hypothesis="Loss falls below 0.1")["id"]
    r = R.create_run(p, hpc, command="sbatch job.sh", backend="slurm", status="completed", exit_code=0, slurm_job_id="4242")["id"]
    remote._set_hpc_state(p, r, state="collected")
    for n in ("loss.csv", "curve.png"):
        R.register_artifact(p, f"/cluster/scratch/pravah/{n}", run=r, allow_missing=True)
    services.set_experiment_status(p, hpc, "needs_review", "all runs finished")
    yield p, planned, hpc, r, str(tmp_path)
    p.close()


def items(root):
    s, call = call_for(root)
    p = s.project(sync=False)
    return IN.gather(call, p, call("tree")), p, call


# ----------------------------------------------------------------------------------------------- the PRAVAH scenario as data
def test_pravah_scenario_is_explained_not_hidden(pravah):
    p, planned, hpc, r, root = pravah
    its, p2, call = items(root)
    by = {i["key"]: i for i in its}
    syn = by[f"synthesis:{hpc}"]
    assert syn["level"] == "followup" and not syn["blocks"] and "not a decision request" in syn["why"]
    assert {"prepare_analyst", "launch_analyst", "record_synthesis", "check"} <= set(syn["actions"])
    art = by[f"artifacts:{hpc}"]
    assert art["level"] == "followup" and "inaccessible" in art["title"] and "external storage not reachable" in art["why"]
    assert "does not prove the file exists" in art["why"] and "collected" in art["why"] and "Nothing was rewritten" in art["why"]
    assert {"recheck", "hpc_sync", "artifacts"} <= set(art["actions"])
    plan = by[f"experiment:{planned}"]
    assert plan["level"] == "info" and "normal starting state" in plan["why"]
    assert not [i for i in its if i["kind"] == "decision"] and call("decision_list", status=None) == []     # the Decisions tab is truly empty
    assert IN.counts(its) == {"action": 0, "followup": 2, "info": 1}
    assert [i["level"] for i in its] == sorted((i["level"] for i in its), key=lambda l: IN.ORDER[l])


def test_artifact_states_and_the_reason_for_each(tmp_path):
    p = research.init(str(tmp_path), name="A")
    e = services.create_experiment(p, "E", hypothesis="h")["id"]
    r = R.create_run(p, e, command="x", backend="local", status="completed", exit_code=0)["id"]
    ok = tmp_path / "fine.csv"
    ok.write_text("a,b\n1,2\n")
    R.register_artifact(p, str(ok), run=r)
    gone = tmp_path / "ext"
    gone.mkdir()
    (gone / "lost.csv").write_text("x")
    R.register_artifact(p, str(gone / "lost.csv"), run=r)
    (gone / "lost.csv").unlink()
    chg = gone / "changed.csv"
    chg.write_text("old")
    R.register_artifact(p, str(chg), run=r)
    chg.write_text("new")
    R.register_artifact(p, "/no/such/mount/far.csv", run=r, allow_missing=True)
    st = {x["name"]: x["state"] for x in IN.artifact_rows(p, e)}
    assert st == {"fine.csv": "local", "lost.csv": "missing", "changed.csv": "modified", "far.csv": "inaccessible"}
    its, _, _ = items(str(tmp_path))
    assert [i for i in its if i["kind"] == "artifacts"][0]["level"] == "action", "a modified or missing file is evidence you cannot trust: that needs you"


def test_agent_proposed_experiment_is_distinguished_from_a_planned_one(tmp_path):
    p = research.init(str(tmp_path), name="G")
    a = services.create_experiment(p, "By agent", hypothesis="h")["id"]
    h = services.create_experiment(p, "By human", hypothesis="h")["id"]
    p.conn.execute("UPDATE experiments SET author_type='agent' WHERE id=?", (a,))
    p.conn.commit()
    its, _, _ = items(str(tmp_path))
    by = {i["ref"]: i for i in its if i["kind"] == "experiment"}
    assert by[a]["level"] == "followup" and "mark_ready" in by[a]["actions"] and "abandon" in by[a]["actions"] and "agent" in by[a]["title"]
    assert by[h]["level"] == "info" and by[h]["actions"] == ["open", "help"]


def test_open_decision_blocks_and_comes_first_and_one_failing_source_does_not_empty_the_inbox(pravah, monkeypatch):
    p, planned, hpc, r, root = pravah
    D.request(p, "Which way?", [{"key": "1", "label": "A"}, {"key": "2", "label": "B"}], subject="T-1")
    monkeypatch.setattr(IN, "_findings", lambda tree, items: (_ for _ in ()).throw(RuntimeError("disk gone")))
    its, _, _ = items(root)
    assert its[0]["kind"] == "decision" and its[0]["level"] == "action" and its[0]["blocks"] and its[0]["tab"] == "t5"
    assert any(i["kind"] == "source" and "findings" in i["title"] and "disk gone" in i["why"] for i in its), "a stale or unreadable source is said out loud"
    assert any(i["kind"] == "synthesis" for i in its)


def test_synthesis_draft_contains_only_recorded_facts(pravah):
    p, planned, hpc, r, root = pravah
    its, p2, call = items(root)
    d = IN.synthesis_draft(p2, call, hpc)
    assert "Loss falls below 0.1" in d and r in d and "slurm" in d and "inaccessible" in d
    assert "we conclude" not in d.lower() and "supports" not in d.lower(), "no invented interpretation"


# ----------------------------------------------------------------------------------------------- lifecycle and help
def test_experiment_context_explains_the_lifecycle_and_who_does_what(pravah):
    p, planned, hpc, r, root = pravah
    its, p2, call = items(root)
    t = " ".join(IN.experiment_context(p2, call, hpc).plain.split())      # detail lines wrap to the pane width
    assert "Lifecycle" in t and "needs_review" in t and "not automatic" in t and "Who does what" in t and "an analyst interprets the results" in t and "You" in t
    assert "1 run(s) not yet synthesized" in t and r in t and "inaccessible" in t


def test_help_explains_every_experiment_status_and_the_loops():
    status = helptext.body("status")
    for s in schema.STATUSES["experiment"]:
        assert s in status or s in IN.LIFECYCLE_WHY
    start = helptext.body("start")
    assert "Question -> Experiment -> Run -> Evidence -> Synthesis -> Finding -> Decision" in start and "Plan -> Task -> Agent -> Verification -> Promotion" in start
    assert "NOT a decision request" in start and "PIN" in helptext.body("review") and "never automatic" in helptext.body("who").lower()
    assert helptext.section_for("synthesis") == "who" and helptext.section_for("nonsense") == "start"
    assert "Inbox" in helptext.body("keys") or "i " in helptext.body("keys")


# ----------------------------------------------------------------------------------------------- the TUI
def test_inbox_tab_shows_what_the_decisions_tab_does_not(pravah):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)

    async def then(pilot):
        bar = text(app, "#status")
        assert "2 recommended" in bar and "(i: Inbox)" in bar and "need" not in bar.split("recommended")[0]
        assert "(2)" in str(app.query_one("#tabs").get_tab("t8").label)
        await pilot.press("6")
        await pilot.pause()
        assert app.query_one("#decisions").row_count == 0 and "No decisions waiting" in text(app, "#decisions-d")
        await pilot.press("i")
        await pilot.pause()
        assert app.query_one("#tabs").active == "t8" and app.query_one("#inbox").row_count == 3
        d = text(app, "#inbox-d")
        assert "Recommended follow-up" in d and "Blocks work" in d and "Why" in d and "Next" in d
        assert "Lifecycle" in d or "inaccessible" in d
    go(app, then)


def test_prepare_is_not_launch_and_launch_needs_confirmation(pravah, monkeypatch):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)
    calls = []
    real = ResearchTUI.call

    def fake(self, name, **kw):
        if name == "dispatch":
            calls.append((name, kw))
            return {"execution_id": "EX-1", "state": "prepared"}
        if name == "execution_show":
            return {"state": "prepared"}
        if name == "execution_start":
            calls.append((name, kw))
            return {"started": True}
        return real(self, name, **kw)
    monkeypatch.setattr(ResearchTUI, "call", fake)

    async def select_synthesis(pilot):
        await pilot.press("i")
        await pilot.pause()
        t = app.query_one("#inbox")
        t.move_cursor(row=t.get_row_index(f"synthesis:{hpc}"))
        await pilot.pause()

    async def then(pilot):
        await select_synthesis(pilot)
        await pilot.press("x")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ActionMenu"
        await pilot.press("enter")                                           # Prepare analyst draft
        await pilot.pause()
        assert calls == [("dispatch", {"role": "analyst", "target": hpc, "headless": True})] and app.prepared == {hpc: "EX-1"}
        assert "NOT running" in text(app, "#inbox-d"), "a prepared session is not a running agent"
        await select_synthesis(pilot)
        await pilot.press("x", "down", "enter")                              # Launch analyst
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Confirm" and len(calls) == 1, "nothing starts before you confirm"
        await pilot.press("n")
        await pilot.pause()
        assert len(calls) == 1
        await select_synthesis(pilot)
        await pilot.press("x", "down", "enter", "y")
        await pilot.pause()
        assert calls[-1] == ("execution_start", {"id": "EX-1"})
    go(app, then)


def test_record_synthesis_is_edited_by_you_and_never_closes_the_experiment(pravah):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("i")
        await pilot.pause()
        t = app.query_one("#inbox")
        t.move_cursor(row=t.get_row_index(f"synthesis:{hpc}"))
        await pilot.pause()
        await pilot.press("x", "down", "down", "down", "enter")              # Record synthesis
        await pilot.pause()
        assert app.screen.__class__.__name__ == "TextBox"
        box = app.screen.query_one("#box")
        assert "Hypothesis (recorded): Loss falls below 0.1" in box.text and "inaccessible" in box.text
        await pilot.press("ctrl+s")                                          # unchanged draft: refused, nothing recorded
        await pilot.pause()
        assert app.screen.__class__.__name__ != "Confirm"
        assert not p.q("SELECT id FROM syntheses WHERE experiment_id=?", (hpc,))
        await pilot.press("x", "down", "down", "down", "enter")
        await pilot.pause()
        app.screen.query_one("#box").text += "The run converged, but the artifacts are unverified, so I only call it preliminary."
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Confirm"
        await pilot.press("y")
        await pilot.pause()
    go(app, then)
    rows = p.q("SELECT id, interpretation FROM syntheses WHERE experiment_id=?", (hpc,))
    assert len(rows) == 1 and "only call it preliminary" in rows[0]["interpretation"]
    assert p.get("experiment", hpc)["status"] == "needs_review", "recording a synthesis never marks the experiment completed"


def test_mark_completed_asks_first(pravah):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)
    app.srv = None

    async def then(pilot):
        item = {"ref": hpc, "kind": "synthesis", "tab": "t6"}
        app.run_item_action(item, "mark_completed")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Confirm" and "accepting its synthesis" in str(app.screen.text)
        await pilot.press("n")
        await pilot.pause()
        assert p.get("experiment", hpc)["status"] == "needs_review"
        app.run_item_action(item, "mark_completed")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    go(app, then)
    assert p.get("experiment", hpc)["status"] == "completed"


def test_artifact_diagnostics_and_hpc_sync_are_reachable_from_the_inbox(pravah, monkeypatch):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)
    calls = []
    real = ResearchTUI.call
    monkeypatch.setattr(ResearchTUI, "call", lambda self, name, **kw: (calls.append(name) or {"outcome": "synced"}) if name == "hpc_sync" else real(self, name, **kw))

    async def then(pilot):
        await pilot.press("i")
        await pilot.pause()
        t = app.query_one("#inbox")
        t.move_cursor(row=t.get_row_index(f"artifacts:{hpc}"))
        await pilot.pause()
        await pilot.press("x", "enter")                                      # first action: show artifacts
        await pilot.pause()
        d = text(app, "#inbox-d")
        assert "loss.csv" in d and "curve.png" in d and d.count("inaccessible") >= 2 and "cannot reach" in d
        await pilot.press("x", "down", "down", "enter")                      # Sync HPC runs
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Confirm" and calls == []
        await pilot.press("y")
        await pilot.pause()
        assert calls == ["hpc_sync"]
    go(app, then)


def test_records_tab_shows_the_lifecycle_for_an_experiment(pravah):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("3")
        await pilot.pause()
        t = app.query_one("#records")
        t.move_cursor(row=t.get_row_index(hpc))
        await pilot.pause()
        d = text(app, "#records-d")
        assert "Lifecycle" in d and "needs_review" in d and "inaccessible" in d
    go(app, then)


def test_contextual_help_opens_at_the_right_section_and_steps(pravah):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)

    async def then(pilot):
        await pilot.press("i")
        await pilot.pause()
        t = app.query_one("#inbox")
        t.move_cursor(row=t.get_row_index(f"synthesis:{hpc}"))
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Help" and "Who does what" in str(app.screen.query_one("#help-title").render())
        assert "AGENT-ASSISTED" in str(app.screen.query_one("#help-body").render())
        await pilot.press("right")
        await pilot.pause()
        assert "Reviews and approvals" in str(app.screen.query_one("#help-title").render())
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "Help"
        await pilot.press("question_mark")
        await pilot.pause()
        assert "Start here" in str(app.screen.query_one("#help-title").render())
    go(app, then)


def test_narrow_terminal_and_empty_project(tmp_path, pravah):
    p, planned, hpc, r, root = pravah
    app = ResearchTUI(root)

    async def then(pilot):
        assert app.has_class("narrow")
        await pilot.press("i")
        await pilot.pause()
        assert app.query_one("#inbox").row_count == 3 and "Why" in text(app, "#inbox-d")
        await pilot.press("question_mark")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Help"
    go(app, then, size=SMALL)
    empty = tmp_path / "empty"
    empty.mkdir()
    research.init(str(empty), name="Empty").close()
    app2 = ResearchTUI(str(empty))

    async def then2(pilot):
        assert "nothing needs you" in text(app2, "#status")
        await pilot.press("i")
        await pilot.pause()
        assert app2.query_one("#inbox").row_count == 0 and "Nothing needs you right now" in text(app2, "#inbox-d")
        await pilot.press("x")                                               # no selection: a message, not a crash
    go(app2, then2, size=BIG)
